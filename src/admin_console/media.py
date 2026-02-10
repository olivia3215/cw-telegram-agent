# src/admin_console/media.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Media editor routes and functionality for the admin console.
"""

import asyncio
import logging
import traceback
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file  # pyright: ignore[reportMissingImports]
from telethon import TelegramClient  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.messages import GetStickerSetRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import (  # pyright: ignore[reportMissingImports]
    InputStickerSetShortName,
)

from clock import clock
from datetime import UTC
from admin_console.helpers import (
    add_cache_busting_headers,
    find_media_file,
    get_state_media_path,
    is_state_media_directory,
    resolve_media_path,
)
from admin_console.sticker_import import import_sticker_set_async
from admin_console.puppet_master import (
    PuppetMasterNotConfigured,
    PuppetMasterUnavailable,
    get_puppet_master_manager,
)
from media.media_budget import reset_description_budget
from media.media_source import (
    AIChainMediaSource,
    AIGeneratingMediaSource,
    BudgetExhaustedMediaSource,
    CompositeMediaSource,
    MediaStatus,
    MEDIA_FILE_EXTENSIONS,
    UnsupportedFormatMediaSource,
    get_default_media_source_chain,
    get_emoji_unicode_name,
)
from media.media_sources import get_directory_media_source
from media.mime_utils import (
    detect_mime_type_from_bytes,
    get_mime_type_from_file_extension,
    is_tgs_mime_type,
    is_video_mime_type,
)
from telegram_download import download_media_bytes
from telegram_media import get_unique_id
from telegram.client_factory import get_telegram_client

logger = logging.getLogger(__name__)

# Create media blueprint
media_bp = Blueprint("media", __name__)


async def _query_sticker_set_info(
    client: TelegramClient, sticker_set_name: str
) -> tuple[bool | None, str | None]:
    """
    Query Telegram API to get sticker set information.
    
    Args:
        client: Telegram client
        sticker_set_name: Short name of the sticker set
        
    Returns:
        Tuple of (is_emoji_set, sticker_set_title)
        - is_emoji_set: True if it's an emoji set, False if it's a regular sticker set, None if unable to determine
        - sticker_set_title: The title/long name of the sticker set, or None if unable to determine
    """
    if not sticker_set_name:
        return (None, None)
    
    try:
        result = await client(
            GetStickerSetRequest(
                stickerset=InputStickerSetShortName(short_name=sticker_set_name),
                hash=0,
            )
        )
        
        # Extract title and emoji set status from the result
        is_emoji_set = False
        sticker_set_title = None
        
        set_obj = getattr(result, "set", None)
        if set_obj:
            # Get the title
            sticker_set_title = getattr(set_obj, "title", None)
            if not sticker_set_title:
                # Fallback to short_name if title is not available
                sticker_set_title = getattr(set_obj, "short_name", None)
            
            # Check for emoji set indicators in the set object
            # Emoji sets typically have emoji=True or a specific set type
            if hasattr(set_obj, "emojis") and getattr(set_obj, "emojis", False):
                is_emoji_set = True
            # Check set type attribute if available
            set_type = getattr(set_obj, "set_type", None)
            if set_type:
                # Check if set_type indicates emoji (varies by Telethon version)
                type_str = str(set_type)
                if "emoji" in type_str.lower() or "Emoji" in type_str:
                    is_emoji_set = True
        
        return (is_emoji_set, sticker_set_title)
    except Exception as e:
        logger.warning(f"Failed to query Telegram for sticker set {sticker_set_name}: {e}")
        return (None, None)

@media_bp.route("/api/media")
def api_media_list():
    """Get list of media files in a directory with pagination and search support.
    
    Query Parameters:
        directory (str, required): Path to the media directory to list
        page (int, optional): Page number for pagination (default: 1)
        page_size (int, optional): Number of items per page (default: 10, max: 100)
        limit (int, optional): Constrains working set to N most recent items (applied before pagination)
        search (str, optional): Search query to filter media by ID, sticker set, sticker name, or description
        media_type (str, optional): Filter by media type (default: "all")
            Valid values: "all", "stickers", "emoji", "video", "photos", "audio", "other"
    
    Returns:
        JSON response with:
        - media_files: List of media items for the current page
        - grouped_media: Media items grouped by sticker set
        - directory: The directory path that was queried
        - pagination: Pagination metadata including page, total_pages, total_items, etc.
    
    Processing Order:
        1. If limit specified, get the N most recent items (by updated_at/modification time)
        2. Apply media type filter within those items (or all items if no limit)
        3. Apply search filter within those items
        4. Paginate the filtered results
    """
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = resolve_media_path(directory_path)
        # Ensure media_dir is a Path object
        if not isinstance(media_dir, Path):
            media_dir = Path(media_dir)
        if not media_dir.exists():
            return jsonify({"error": "Directory not found"}), 404

        # Check if this is the state/media directory (always use MySQL for state/media)
        is_state_media = is_state_media_directory(media_dir)
        state_media_path = get_state_media_path()

        # Use MediaSource API to read media descriptions
        # For state/media, use the default chain (includes MySQLMediaSource)
        # For other directories, use directory source only
        if is_state_media:
            # Use the default media source chain which includes MySQL
            media_chain = get_default_media_source_chain()
            cache_source = None  # Not used when MySQL is enabled
        else:
            # Create a chain with DirectoryMediaSource and UnsupportedFormatMediaSource
            # but without AIGeneratingMediaSource (no AI generation in listing)
            cache_source = get_directory_media_source(media_dir)
            unsupported_source = UnsupportedFormatMediaSource()
            media_chain = CompositeMediaSource(
                [
                    cache_source,
                    unsupported_source,
                ]
            )

        # Get pagination parameters
        page = 1
        page_str = request.args.get("page", "").strip()
        if page_str:
            try:
                page = int(page_str)
                if page < 1:
                    page = 1
            except ValueError:
                page = 1
        
        page_size = 10
        page_size_str = request.args.get("page_size", "").strip()
        if page_size_str:
            try:
                page_size = int(page_size_str)
                if page_size < 1:
                    page_size = 10
                elif page_size > 100:  # Cap at 100 to prevent abuse
                    page_size = 100
            except ValueError:
                page_size = 10

        # Get optional limit parameter (constrains working set to N most recent)
        limit_str = request.args.get("limit", "").strip()
        limit = None
        if limit_str:
            try:
                limit = int(limit_str)
                if limit < 1:
                    limit = None
            except ValueError:
                limit = None

        # Get search parameter
        search_query = request.args.get("search", "").strip()
        if not search_query:
            search_query = None

        # Get media type filter
        media_type = request.args.get("media_type", "all").strip().lower()
        valid_media_types = ["all", "stickers", "emoji", "video", "photos", "audio", "other"]
        if media_type not in valid_media_types:
            media_type = "all"

        media_files = []

        # Create a single event loop for all async operations in this request
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Get unique IDs with filtering applied at database level for MySQL
            unique_ids = []
            total_count = 0  # Total count matching filters (for pagination)
            use_mysql = is_state_media
            
            if is_state_media:
                # For MySQL, build query with limit, media_type, search, and pagination
                try:
                    from db.connection import get_db_connection
                    with get_db_connection() as conn:
                        cursor = conn.cursor()
                        try:
                            # Build WHERE clause for filters
                            where_clauses = []
                            params = []
                            
                            # Apply media type filter
                            if media_type != "all":
                                if media_type == "stickers":
                                    where_clauses.append("(kind IN ('sticker', 'animated_sticker') AND (is_emoji_set IS NULL OR is_emoji_set = 0))")
                                elif media_type == "emoji":
                                    where_clauses.append("(kind IN ('sticker', 'animated_sticker') AND is_emoji_set = 1)")
                                elif media_type == "video":
                                    where_clauses.append("kind IN ('video', 'animation')")
                                elif media_type == "photos":
                                    where_clauses.append("kind = 'photo'")
                                elif media_type == "audio":
                                    where_clauses.append("kind = 'audio'")
                                elif media_type == "other":
                                    where_clauses.append("kind NOT IN ('sticker', 'animated_sticker', 'video', 'animation', 'photo', 'audio')")
                            
                            # Apply search filter
                            if search_query:
                                search_pattern = f"%{search_query}%"
                                where_clauses.append(
                                    "(unique_id LIKE %s OR description LIKE %s OR sticker_set_name LIKE %s OR sticker_name LIKE %s)"
                                )
                                params.extend([search_pattern, search_pattern, search_pattern, search_pattern])
                            
                            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
                            
                            # Note: where_sql is constructed from hardcoded clauses only (media_type is validated
                            # against a whitelist) and parameterized search patterns. No user input is interpolated
                            # directly into the SQL string, making this safe from SQL injection.
                            
                            # Get total count matching filters (within limit if specified)
                            if limit:
                                # Count only within the N most recent items
                                # Use updated_at for sorting (most recent first)
                                # IMPORTANT: Must select all columns needed for WHERE clause
                                count_sql = f"""
                                    SELECT COUNT(*) as total FROM (
                                        SELECT unique_id, kind, is_emoji_set, description, 
                                               sticker_set_name, sticker_name
                                        FROM media_metadata
                                        ORDER BY updated_at DESC
                                        LIMIT %s
                                    ) AS limited
                                    WHERE {where_sql}
                                """
                                cursor.execute(count_sql, [limit] + params)
                            else:
                                count_sql = f"SELECT COUNT(*) as total FROM media_metadata WHERE {where_sql}"
                                cursor.execute(count_sql, params)
                            
                            total_count = cursor.fetchone()["total"]
                            
                            # Get paginated unique_ids
                            offset = (page - 1) * page_size
                            
                            if limit:
                                # Query within the N most recent items
                                query_sql = f"""
                                    SELECT unique_id FROM (
                                        SELECT unique_id, updated_at, kind, is_emoji_set, description, 
                                               sticker_set_name, sticker_name, mime_type
                                        FROM media_metadata
                                        ORDER BY updated_at DESC
                                        LIMIT %s
                                    ) AS limited
                                    WHERE {where_sql}
                                    ORDER BY updated_at DESC
                                    LIMIT %s OFFSET %s
                                """
                                cursor.execute(query_sql, [limit] + params + [page_size, offset])
                            else:
                                query_sql = f"""
                                    SELECT unique_id FROM media_metadata
                                    WHERE {where_sql}
                                    ORDER BY updated_at DESC
                                    LIMIT %s OFFSET %s
                                """
                                cursor.execute(query_sql, params + [page_size, offset])
                            
                            rows = cursor.fetchall()
                            unique_ids = [row["unique_id"] for row in rows]
                        finally:
                            cursor.close()
                except Exception as e:
                    logger.warning(f"Failed to load unique IDs from MySQL: {e}, falling back to filesystem")
                    use_mysql = False
                    # Fall back to filesystem - initialize cache_source and media_chain
                    cache_source = get_directory_media_source(media_dir)
                    unsupported_source = UnsupportedFormatMediaSource()
                    media_chain = CompositeMediaSource(
                        [
                            cache_source,
                            unsupported_source,
                        ]
                    )
                    # Fall through to filesystem handling below
            
            if not use_mysql:
                # Filesystem implementation: load all, filter, sort, paginate
                all_json_files = list(media_dir.glob("*.json"))
                
                # Load metadata for filtering
                all_items = []
                for json_file in all_json_files:
                    try:
                        unique_id = json_file.stem
                        record = cache_source.get_cached_record(unique_id)
                        if not record:
                            continue
                        
                        # Get file modification time for sorting
                        media_file_path = find_media_file(media_dir, unique_id)
                        mod_time = media_file_path.stat().st_mtime if media_file_path and media_file_path.exists() else 0
                        
                        all_items.append({
                            "unique_id": unique_id,
                            "record": record,
                            "mod_time": mod_time
                        })
                    except Exception as e:
                        logger.debug(f"Error loading {json_file}: {e}")
                        continue
                
                # Sort by modification time (most recent first)
                all_items.sort(key=lambda x: x["mod_time"], reverse=True)
                
                # Apply limit if specified
                if limit:
                    all_items = all_items[:limit]
                
                # Apply media type filter
                if media_type != "all":
                    filtered_items = []
                    for item in all_items:
                        record = item["record"]
                        kind = record.get("kind", "unknown")
                        is_emoji_set = record.get("is_emoji_set", False)
                        
                        if media_type == "stickers" and kind in ("sticker", "animated_sticker") and not is_emoji_set:
                            filtered_items.append(item)
                        elif media_type == "emoji" and kind in ("sticker", "animated_sticker") and is_emoji_set:
                            filtered_items.append(item)
                        elif media_type == "video" and kind in ("video", "animation"):
                            filtered_items.append(item)
                        elif media_type == "photos" and kind == "photo":
                            filtered_items.append(item)
                        elif media_type == "audio" and kind == "audio":
                            filtered_items.append(item)
                        elif media_type == "other" and kind not in ("sticker", "animated_sticker", "video", "animation", "photo", "audio"):
                            filtered_items.append(item)
                    all_items = filtered_items
                
                # Apply search filter
                if search_query:
                    search_lower = search_query.lower()
                    filtered_items = []
                    for item in all_items:
                        record = item["record"]
                        unique_id = item["unique_id"]
                        
                        # Search in unique_id, description, sticker_set_name, sticker_name
                        if (search_lower in unique_id.lower() or
                            search_lower in (record.get("description") or "").lower() or
                            search_lower in (record.get("sticker_set_name") or "").lower() or
                            search_lower in (record.get("sticker_name") or "").lower()):
                            filtered_items.append(item)
                    all_items = filtered_items
                
                total_count = len(all_items)
                
                # Paginate
                offset = (page - 1) * page_size
                paginated_items = all_items[offset:offset + page_size]
                unique_ids = [item["unique_id"] for item in paginated_items]
            
            # Process each unique ID
            for unique_id in unique_ids:
                try:
                    # Use MediaSource chain to get the record (applies all transformations)
                    record = loop.run_until_complete(
                        media_chain.get(unique_id=unique_id)
                    )

                    if not record:
                        logger.warning(f"No record found for {unique_id}")
                        continue

                    # Look for associated media file: (1) use metadata first, (2) glob then patch
                    media_file_path = None
                    media_file_name = record.get("media_file")
                    if media_file_name:
                        bases = [media_dir]
                        if state_media_path:
                            bases.append(state_media_path)
                        for base in bases:
                            if not base or not base.exists():
                                continue
                            candidate = base / media_file_name
                            if candidate.exists() and candidate.is_file() and candidate.suffix.lower() != ".json":
                                media_file_path = candidate
                                break
                    if not media_file_path:
                        media_file_path = find_media_file(media_dir, unique_id)
                        if media_file_path and not record.get("media_file"):
                            # Patch metadata so future lookups are fast (filename relative to metadata dir)
                            try:
                                record["media_file"] = media_file_path.name
                                base_dir = media_file_path.parent
                                if is_state_media_directory(base_dir):
                                    from db import media_metadata
                                    media_metadata.save_media_metadata(record)
                                else:
                                    get_directory_media_source(base_dir).put(unique_id, record)
                            except Exception as e:
                                logger.debug(f"Could not patch media_file for {unique_id}: {e}")
                    media_file = str(media_file_path) if media_file_path else None

                    mime_type = record.get("mime_type")

                    # Attempt to detect MIME type when missing (common for legacy stickers)
                    if (not mime_type) and media_file_path and media_file_path.exists():
                        try:
                            with open(media_file_path, "rb") as media_fp:
                                file_head = media_fp.read(1024)
                            detected_mime_type = detect_mime_type_from_bytes(file_head)
                            if (
                                detected_mime_type == "application/gzip"
                                and media_file_path.suffix.lower() == ".tgs"
                            ):
                                mime_type = "application/x-tgsticker"
                            else:
                                mime_type = detected_mime_type
                            logger.debug(
                                "Detected MIME type %s for %s",
                                mime_type,
                                media_file_path.name,
                            )
                        except Exception as mime_error:  # pragma: no cover - defensive
                            logger.warning(
                                "Failed to detect MIME type for %s: %s",
                                media_file_path,
                                mime_error,
                            )
                            mime_type = record.get("mime_type")
                            if mime_type is None and media_file_path:
                                ext_mime = get_mime_type_from_file_extension(
                                    media_file_path
                                )
                                if ext_mime:
                                    mime_type = ext_mime
                                    logger.debug(
                                        "Used extension fallback MIME %s for %s",
                                        mime_type,
                                        media_file_path.name,
                                    )
                    elif (
                        mime_type == "application/gzip"
                        and media_file_path
                        and media_file_path.suffix.lower() == ".tgs"
                    ):
                        mime_type = "application/x-tgsticker"

                    # Group by sticker set for organization
                    kind = record.get("kind", "unknown")
                    if kind == "sticker" and mime_type:
                        if is_tgs_mime_type(mime_type) or is_video_mime_type(mime_type):
                            kind = "animated_sticker"

                    # Extract sticker_name early so we can use it for emoji set detection
                    sticker_name = record.get("sticker_name", "")

                    if kind == "sticker" or kind == "animated_sticker":
                        sticker_set = record.get("sticker_set_name")
                        sticker_set_title = record.get("sticker_set_title")  # May be None for old records
                        
                        # If sticker has no set name, treat it as regular media based on type
                        if not sticker_set:
                            # Unnamed stickers are treated as images or videos
                            # Check if it's an animated sticker (TGS) or a video format (like converted WebM)
                            if kind == "animated_sticker" or is_tgs_mime_type(mime_type) or (mime_type and is_video_mime_type(mime_type)):
                                sticker_set = "Other Media - Videos"
                            else:
                                sticker_set = "Other Media - Images"
                            sticker_set_title = None
                            is_emoji_set = False
                        else:
                            # Check if we already have cached is_emoji_set and sticker_set_title
                            is_emoji_set = record.get("is_emoji_set")
                            if sticker_set_title is None:
                                sticker_set_title = record.get("sticker_set_title")
                            need_to_cache = False
                            
                            # Query Telegram if we're missing either piece of information
                            if is_emoji_set is None or sticker_set_title is None:
                                try:
                                    # Get puppet master client for Telegram queries
                                    puppet_master = get_puppet_master_manager()
                                    if puppet_master.is_configured:
                                        # Use puppet master's run() method to execute in its event loop
                                        def _query_factory(client: TelegramClient):
                                            return _query_sticker_set_info(client, sticker_set)
                                        
                                        queried_is_emoji, queried_title = puppet_master.run(_query_factory, timeout=10)
                                        
                                        # Use queried values if we got them
                                        if is_emoji_set is None and queried_is_emoji is not None:
                                            is_emoji_set = queried_is_emoji
                                            need_to_cache = True
                                        if sticker_set_title is None and queried_title is not None:
                                            sticker_set_title = queried_title
                                            need_to_cache = True
                                except Exception as e:
                                    logger.warning(f"Failed to query sticker set info for {sticker_set}: {e}")
                                
                                # Default to False for is_emoji_set if we couldn't determine
                                if is_emoji_set is None:
                                    is_emoji_set = False
                            
                            # Cache the information if we just queried for it
                            if need_to_cache:
                                if is_emoji_set is not None:
                                    record["is_emoji_set"] = is_emoji_set
                                if sticker_set_title is not None:
                                    record["sticker_set_title"] = sticker_set_title
                                # Save to MySQL or filesystem
                                if use_mysql:
                                    from db import media_metadata
                                    media_metadata.save_media_metadata(record)
                                else:
                                    cache_source.put(unique_id, record)
                    else:
                        # For non-stickers, create categorized "Other Media" groups
                        if kind == "photo":
                            sticker_set = "Other Media - Images"
                        elif kind in ("video", "animation"):
                            sticker_set = "Other Media - Videos"
                        elif kind == "audio":
                            sticker_set = "Other Media - Audio"
                        else:
                            # Unknown kinds: treat as images if image MIME type, otherwise generic "Other Media"
                            if mime_type and mime_type.startswith("image/"):
                                sticker_set = "Other Media - Images"
                            elif mime_type and (mime_type.startswith("video/") or is_tgs_mime_type(mime_type)):
                                sticker_set = "Other Media - Videos"
                            elif mime_type and mime_type.startswith("audio/"):
                                sticker_set = "Other Media - Audio"
                            else:
                                sticker_set = "Other Media"
                        sticker_set_title = None
                        is_emoji_set = False

                    # Add emoji description for sticker names
                    emoji_description = ""
                    if sticker_name and kind in ("sticker", "animated_sticker"):
                        try:
                            emoji_description = get_emoji_unicode_name(sticker_name)
                        except Exception:
                            emoji_description = ""

                    # Determine json_file path (for display purposes)
                    json_file_path = media_dir / f"{unique_id}.json" if not use_mysql else None
                    
                    # Get file creation time for sorting (by media file creation date)
                    file_creation_time = None
                    if media_file_path and media_file_path.exists():
                        try:
                            file_creation_time = media_file_path.stat().st_mtime
                        except Exception as e:
                            logger.debug(f"Failed to get file creation time for {media_file_path}: {e}")
                    
                    media_item = {
                        "unique_id": unique_id,
                        "json_file": str(json_file_path) if json_file_path else None,
                        "media_file": media_file,
                        "description": record.get("description"),
                        "kind": kind,
                        "sticker_set_name": sticker_set,
                        "sticker_set_title": sticker_set_title,
                        "sticker_name": sticker_name,
                        "emoji_description": emoji_description,
                        "is_emoji_set": is_emoji_set,
                        "status": record.get("status", "unknown"),
                        "failure_reason": record.get("failure_reason"),
                        "mime_type": mime_type,
                        "_file_creation_time": file_creation_time,  # Internal field for sorting
                    }
                    
                    media_files.append(media_item)

                except Exception as e:
                    logger.error(f"Error processing {unique_id}: {e}")
                    continue
        finally:
            loop.close()

        # Remove internal sorting field before returning (no longer needed as we don't sort here)
        for media in media_files:
            media.pop("_file_creation_time", None)

        # Group by sticker set
        grouped_media = {}
        for media in media_files:
            sticker_set = media["sticker_set_name"]
            if sticker_set not in grouped_media:
                grouped_media[sticker_set] = []
            grouped_media[sticker_set].append(media)

        # Calculate pagination metadata
        total_pages = (total_count + page_size - 1) // page_size if total_count > 0 else 0
        
        response = jsonify(
            {
                "media_files": media_files,
                "grouped_media": grouped_media,
                "directory": directory_path,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total_items": total_count,
                    "total_pages": total_pages,
                    "limit": limit,
                    "search": search_query,
                    "media_type": media_type if media_type != "all" else None,
                    "has_more": page < total_pages
                }
            }
        )
        return add_cache_busting_headers(response)

    except Exception as e:
        logger.error(f"Error listing media files: {e}")
        return jsonify({"error": str(e)}), 500


@media_bp.route("/api/media/<unique_id>")
def api_media_file(unique_id: str):
    """Serve a media file."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = resolve_media_path(directory_path)
        # Ensure media_dir is a Path object
        if not isinstance(media_dir, Path):
            media_dir = Path(media_dir)

        is_state_media = is_state_media_directory(media_dir)
        state_media_path = get_state_media_path()

        # (1) Use media_file from metadata first; (2) if missing, glob then patch
        media_file = None
        record = None
        if is_state_media:
            from db import media_metadata
            record = media_metadata.load_media_metadata(unique_id)
        else:
            record = get_directory_media_source(media_dir).get_cached_record(unique_id)
        if record and record.get("media_file"):
            bases = [media_dir]
            if state_media_path:
                bases.append(state_media_path)
            for base in bases:
                if not base or not base.exists():
                    continue
                candidate = base / record["media_file"]
                if candidate.exists() and candidate.is_file() and candidate.suffix.lower() != ".json":
                    media_file = candidate
                    break
        if not media_file:
            media_file = find_media_file(media_dir, unique_id)
            if media_file and record and not record.get("media_file"):
                try:
                    record["media_file"] = media_file.name
                    base_dir = media_file.parent
                    if is_state_media_directory(base_dir):
                        from db import media_metadata
                        media_metadata.save_media_metadata(record)
                    else:
                        get_directory_media_source(base_dir).put(unique_id, record)
                except Exception as e:
                    logger.debug(f"Could not patch media_file for {unique_id}: {e}")
        if media_file:
            # Use MIME sniffing to detect the correct MIME type
            try:
                with open(media_file, "rb") as f:
                    file_bytes = f.read(1024)  # Read first 1KB for MIME detection
                detected_mime_type = detect_mime_type_from_bytes(file_bytes)
                return send_file(media_file, mimetype=detected_mime_type)
            except Exception as e:
                logger.warning(
                    f"Failed to detect MIME type for {media_file}, falling back to default: {e}"
                )
                return send_file(media_file)

        return jsonify({"error": "Media file not found"}), 404

    except Exception as e:
        logger.error(f"Error serving media file {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@media_bp.route("/api/media/<unique_id>/description", methods=["PUT"])
def api_update_description(unique_id: str):
    """Update a media description."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = resolve_media_path(directory_path)
        # Ensure media_dir is a Path object
        if not isinstance(media_dir, Path):
            media_dir = Path(media_dir)
        
        # Check if this is the state/media directory and MySQL backend is enabled
        # Always use MySQL for state/media
        is_state_media = is_state_media_directory(media_dir)
        
        if is_state_media:
            # Load from MySQL
            from db import media_metadata
            record = media_metadata.load_media_metadata(unique_id)
            if not record:
                return jsonify({"error": "Media record not found"}), 404
            
            # Update description
            new_description = request.json.get("description", "").strip()
            record["description"] = new_description if new_description else None
            
            # Clear error fields if description is provided
            if new_description:
                record.pop("failure_reason", None)
                record["status"] = "curated"  # Mark as curated when user edits description
            
            # Save to MySQL
            media_metadata.save_media_metadata(record)
        else:
            # Use filesystem
            source = get_directory_media_source(media_dir)
            record = source.get_cached_record(unique_id)

            if not record:
                return jsonify({"error": "Media record not found"}), 404

            # Update description
            new_description = request.json.get("description", "").strip()
            record["description"] = new_description if new_description else None

            # Clear error fields if description is provided
            if new_description:
                record.pop("failure_reason", None)
                record["status"] = "curated"  # Mark as curated when user edits description

            source.put(unique_id, record)

        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error updating description for {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@media_bp.route("/api/media/<unique_id>/refresh-ai", methods=["POST"])
def api_refresh_from_ai(unique_id: str):
    """Refresh description using AI pipeline."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = resolve_media_path(directory_path)
        # Ensure media_dir is a Path object
        if not isinstance(media_dir, Path):
            media_dir = Path(media_dir)
        
        # Always use MySQL for state/media
        is_state_media = is_state_media_directory(media_dir)
        
        # Load the record - use MySQL for state/media, otherwise filesystem
        if is_state_media:
            from db import media_metadata
            data = media_metadata.load_media_metadata(unique_id)
        else:
            media_cache_source = get_directory_media_source(media_dir)
            data = media_cache_source.get_cached_record(unique_id)

        if not data:
            return jsonify({"error": "Media record not found"}), 404

        # Force the AI pipeline to regenerate a fresh description.
        logger.debug(
            "Refresh-from-AI: clearing cached description for %s in %s",
            unique_id,
            media_dir,
        )
        data["description"] = None
        data.pop("failure_reason", None)
        data["status"] = MediaStatus.TEMPORARY_FAILURE.value
        
        # Save the updated record using the appropriate backend
        if is_state_media:
            from db import media_metadata
            media_metadata.save_media_metadata(data)
        else:
            media_cache_source = get_directory_media_source(media_dir)
            media_cache_source.put(unique_id, data)

        # Use the puppetmaster's client for media operations
        # (The client isn't actually used when doc is a Path, but the interface requires it)
        # Create a minimal agent-like object
        class MinimalAgent:
            def __init__(self, client):
                self.client = client
                self.name = "puppetmaster"
        
        # Require puppetmaster for media operations
        try:
            puppet_master = get_puppet_master_manager()
            puppet_master.ensure_ready()
        except (PuppetMasterNotConfigured, PuppetMasterUnavailable) as e:
            logger.error(f"Puppetmaster required for media operations but not available: {e}")
            return jsonify({
                "error": f"Puppetmaster is required for media operations. {str(e)}"
            }), 400
        
        # Find the media file: (1) use metadata first; (2) if missing, glob then patch
        state_media_path = get_state_media_path()
        media_file = None
        if data.get("media_file"):
            bases = [media_dir]
            if state_media_path:
                bases.append(state_media_path)
            for base in bases:
                if not base or not base.exists():
                    continue
                candidate = base / data["media_file"]
                if candidate.exists() and candidate.is_file() and candidate.suffix.lower() != ".json":
                    media_file = candidate
                    break
        if not media_file:
            media_file = find_media_file(media_dir, unique_id)
            if media_file and not data.get("media_file"):
                try:
                    data["media_file"] = media_file.name
                    base_dir = media_file.parent
                    if is_state_media_directory(base_dir):
                        from db import media_metadata
                        media_metadata.save_media_metadata(data)
                    else:
                        get_directory_media_source(base_dir).put(unique_id, data)
                except Exception as e:
                    logger.debug(f"Could not patch media_file for {unique_id}: {e}")
        if not media_file:
            return jsonify({"error": "Media file not found"}), 404

        # Reset the media description budget for this refresh request
        # When user explicitly requests refresh, they should be able to get a description
        # regardless of the current budget state
        import config
        reset_description_budget(config.MEDIA_DESC_BUDGET_PER_TICK)
        logger.info(
            f"Refresh-from-AI: reset budget to {config.MEDIA_DESC_BUDGET_PER_TICK} for {unique_id}"
        )

        # Pass the media file Path directly as the doc parameter
        # download_media_bytes supports Path objects directly
        fake_doc = media_file

        # For refresh, use the full AI chain to ensure media files are downloaded
        # Create a proper chain with AIChainMediaSource that handles downloads
        ai_cache_dir = media_dir
        ai_cache_dir.mkdir(parents=True, exist_ok=True)

        # Create the same chain structure as the main application
        # Use MySQL cache source if this is state/media
        if is_state_media:
            from media.mysql_media_source import MySQLMediaSource
            # Create directory_source so MySQLMediaSource can write media files to disk
            directory_source = get_directory_media_source(ai_cache_dir)
            ai_cache_source = MySQLMediaSource(directory_source=directory_source)
        else:
            ai_cache_source = get_directory_media_source(ai_cache_dir)
        
        unsupported_source = UnsupportedFormatMediaSource()
        budget_source = BudgetExhaustedMediaSource()
        ai_source = AIGeneratingMediaSource(cache_directory=ai_cache_dir)

        media_chain = CompositeMediaSource(
            [
                AIChainMediaSource(
                    cache_source=ai_cache_source,
                    unsupported_source=unsupported_source,
                    budget_source=budget_source,
                    ai_source=ai_source,
                )
            ]
        )

        # Determine the correct media kind based on MIME type
        # This fixes the issue where cached records have wrong kind for animated stickers
        mime_type = data.get("mime_type")
        
        # If MIME type is not in data but we have a Path, try to get it from file extension
        if not mime_type and hasattr(fake_doc, "suffix"):
            mime_type = get_mime_type_from_file_extension(fake_doc)
        
        # Determine kind from MIME type if not already set
        if is_tgs_mime_type(mime_type):
            media_kind = "animated_sticker"
        elif mime_type and mime_type == "audio/mp4":
            # Audio-only MP4 files (M4A) should be treated as audio
            media_kind = "audio"
        elif mime_type:
            from media.mime_utils import is_audio_mime_type, is_video_mime_type, is_image_mime_type
            if is_audio_mime_type(mime_type):
                media_kind = "audio"
            elif is_video_mime_type(mime_type):
                media_kind = "video"
            elif is_image_mime_type(mime_type):
                media_kind = "photo"
            else:
                # Fall back to data kind or default
                media_kind = data.get("kind", "sticker")
        else:
            media_kind = data.get("kind", "sticker")

        # Run the media chain in the puppet master's event loop
        async def _refresh_coro(client: TelegramClient):
            agent = MinimalAgent(client)
            logger.info(
                "Refreshing AI description for %s using puppetmaster",
                unique_id,
            )
            
            # For media editor, we don't have a real Telegram document
            # download_media_bytes supports Path objects directly
            record = await media_chain.get(
                unique_id=unique_id,
                agent=agent,
                doc=fake_doc,  # Path object - download_media_bytes handles this
                kind=media_kind,
                sticker_set_name=data.get("sticker_set_name"),
                sticker_name=data.get("sticker_name"),
                sender_id=None,
                sender_name=None,
                channel_id=None,
                channel_name=None,
                media_ts=None,
                duration=data.get(
                    "duration"
                ),  # Include duration for video/animated stickers
                mime_type=mime_type,  # Pass MIME type in metadata so it's available early
                skip_fallback=True,
            )
            return record
        
        try:
            record = puppet_master.run(_refresh_coro, timeout=120)
        except Exception as e:
            logger.error(f"Failed to refresh AI description: {e}")
            return jsonify({"error": f"Failed to refresh AI description: {e}"}), 500

        if record:
            # AIChainMediaSource has already cached the result to disk
            new_description = record.get("description")
            new_status = record.get("status", "ok")
            logger.info(
                "Got fresh AI description for %s (status=%s): %s",
                unique_id,
                new_status,
                (new_description[:50] + "…") if new_description else "None",
            )
            logger.debug(
                "Refresh-from-AI: updated metadata for %s -> %s",
                unique_id,
                record,
            )
            return jsonify(
                {"success": True, "description": new_description, "status": new_status}
            )
        else:
            logger.warning(f"No AI description generated for {unique_id}")
            return jsonify({"error": "No AI description generated"}), 500

    except Exception as e:
        logger.error(f"Error refreshing AI description for {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@media_bp.route("/api/media/<unique_id>/move", methods=["POST"])
def api_move_media(unique_id: str):
    """Move a media item from one directory to another."""
    try:
        from_directory = request.args.get("from_directory")
        to_directory = request.args.get("to_directory")

        if not from_directory or not to_directory:
            return (
                jsonify({"error": "Missing from_directory or to_directory parameter"}),
                400,
            )

        from_dir = resolve_media_path(from_directory)
        to_dir = resolve_media_path(to_directory)

        # Ensure directories are Path objects
        if not isinstance(from_dir, Path):
            from_dir = Path(from_dir)
        if not isinstance(to_dir, Path):
            to_dir = Path(to_dir)

        # Check if source or destination is state/media (which uses MySQL)
        is_from_state_media = is_state_media_directory(from_dir)
        is_to_state_media = is_state_media_directory(to_dir)

        # Handle no-op case (moving state/media to itself)
        if is_from_state_media and is_to_state_media:
            # Verify record exists before returning success (consistent with other branches)
            from db import media_metadata
            record = media_metadata.load_media_metadata(unique_id)
            if not record:
                return jsonify({"error": "Media record not found"}), 404
            logger.info(f"Moving media {unique_id} from state/media to itself (no-op)")
            return jsonify({"success": True})

        if is_from_state_media:
            # Load from MySQL
            from db import media_metadata
            record = media_metadata.load_media_metadata(unique_id)
            if not record:
                return jsonify({"error": "Media record not found"}), 404

            # Get the destination source (for writing JSON file)
            to_source = get_directory_media_source(to_dir)

            # Check if destination already has a record (for rollback purposes)
            original_dest_record = to_source.get_cached_record(unique_id)

            # Determine media file name before moving
            media_file_name = record.get("media_file")
            if not media_file_name:
                # Try common extensions if media_file field is not set
                for ext in MEDIA_FILE_EXTENSIONS:
                    source_media = from_dir / f"{unique_id}{ext}"
                    if source_media.exists():
                        media_file_name = source_media.name
                        record["media_file"] = media_file_name
                        break

            # Write JSON file to destination directory FIRST
            # Filter the record to exclude MySQL-specific or state-specific fields
            # (same filtering as DirectoryMediaSource does for config directories)
            # Only move file after successful write to prevent inconsistent state
            try:
                to_source.put(unique_id, record)
            except Exception as e:
                logger.error(f"Failed to write media {unique_id} to {to_directory}: {e}")
                return jsonify({"error": f"Failed to write to destination: {str(e)}"}), 500

            # Move media file from state/media to destination AFTER successful metadata write
            if media_file_name:
                source_media = from_dir / media_file_name
                if source_media.exists():
                    target_media = to_dir / media_file_name
                    try:
                        to_dir.mkdir(parents=True, exist_ok=True)
                        source_media.replace(target_media)
                        logger.debug(f"Moved media file {media_file_name} from {from_dir} to {to_dir}")
                    except Exception as e:
                        # Rollback: restore original JSON metadata if it existed, or delete JSON if it didn't
                        # Do NOT delete media file since we didn't create it (put() only writes JSON)
                        logger.error(f"Failed to move media file {media_file_name} from {from_dir} to {to_dir}: {e}")
                        try:
                            json_path = to_dir / f"{unique_id}.json"
                            if original_dest_record:
                                # Restore the original JSON file
                                to_source.put(unique_id, original_dest_record)
                                logger.debug(f"Restored original JSON metadata for {unique_id} during rollback")
                            else:
                                # No original record existed, just delete the JSON we created
                                if json_path.exists():
                                    json_path.unlink()
                                # Also remove from memory cache since put() already updated it
                                with to_source._lock:
                                    to_source._mem_cache.pop(unique_id, None)
                                logger.debug(f"Deleted JSON metadata for {unique_id} during rollback")
                        except Exception as rollback_error:
                            logger.error(f"Failed to rollback metadata write for {unique_id}: {rollback_error}")
                        return jsonify({"error": f"Failed to move media file: {str(e)}"}), 500
                else:
                    logger.warning(f"Media file {media_file_name} referenced in metadata for {unique_id} does not exist in {from_dir}")
            else:
                logger.warning(f"No media file found for {unique_id} in {from_dir}")

            # Delete from MySQL only after successful write and file move
            media_metadata.delete_media_metadata(unique_id)
            logger.info(f"Moved media {unique_id} from MySQL ({from_directory}) to {to_directory}")
        elif is_to_state_media:
            # Moving TO state/media (MySQL) from another directory
            # Load from source directory
            from_source = get_directory_media_source(from_dir)
            record = from_source.get_cached_record(unique_id)
            if not record:
                return jsonify({"error": "Media record not found"}), 404

            # Ensure unique_id is in the record
            record["unique_id"] = unique_id

            # Determine media file name before moving
            media_file_name = record.get("media_file")
            if not media_file_name:
                # Try common extensions if media_file field is not set
                for ext in MEDIA_FILE_EXTENSIONS:
                    source_media = from_dir / f"{unique_id}{ext}"
                    if source_media.exists():
                        media_file_name = source_media.name
                        record["media_file"] = media_file_name
                        break

            # Save to MySQL FIRST (filters fields automatically)
            # Only move file after successful save to prevent inconsistent state
            from db import media_metadata
            # Check if MySQL already has a record (for rollback purposes)
            original_mysql_record = media_metadata.load_media_metadata(unique_id)
            try:
                media_metadata.save_media_metadata(record)
            except Exception as e:
                logger.error(f"Failed to save media {unique_id} to MySQL: {e}")
                return jsonify({"error": f"Failed to save to MySQL: {str(e)}"}), 500

            # Move media file from source to state/media AFTER successful metadata save
            if media_file_name:
                source_media = from_dir / media_file_name
                if source_media.exists():
                    target_media = to_dir / media_file_name
                    try:
                        to_dir.mkdir(parents=True, exist_ok=True)
                        source_media.replace(target_media)
                        logger.debug(f"Moved media file {media_file_name} from {from_dir} to {to_dir}")
                    except Exception as e:
                        # Rollback: restore original MySQL record if it existed, or delete if it didn't
                        logger.error(f"Failed to move media file {media_file_name} from {from_dir} to {to_dir}: {e}")
                        try:
                            if original_mysql_record:
                                # Restore the original record
                                media_metadata.save_media_metadata(original_mysql_record)
                                logger.debug(f"Restored original MySQL metadata for {unique_id} during rollback")
                            else:
                                # No original record existed, delete the one we created
                                media_metadata.delete_media_metadata(unique_id)
                                logger.debug(f"Deleted MySQL metadata for {unique_id} during rollback")
                        except Exception as rollback_error:
                            logger.error(f"Failed to rollback metadata save for {unique_id}: {rollback_error}")
                        return jsonify({"error": f"Failed to move media file: {str(e)}"}), 500
                else:
                    logger.warning(f"Media file {media_file_name} referenced in metadata for {unique_id} does not exist in {from_dir}")
            else:
                logger.warning(f"No media file found for {unique_id} in {from_dir}")

            # Delete from source directory only after successful save and file move
            from_source.delete_record(unique_id)
            logger.info(f"Moved media {unique_id} from {from_directory} to MySQL ({to_directory})")
        else:
            # Use filesystem move (existing logic)
            from_source = get_directory_media_source(from_dir)
            to_source = get_directory_media_source(to_dir)

            try:
                from_source.move_record_to(unique_id, to_source)
            except KeyError:
                return jsonify({"error": "Media record not found"}), 404

            logger.info(f"Moved media {unique_id} from {from_directory} to {to_directory}")

        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error moving media {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@media_bp.route("/api/media/<unique_id>/delete", methods=["DELETE"])
def api_delete_media(unique_id: str):
    """Delete a media item and its description."""
    try:
        directory_path = request.args.get("directory")
        if not directory_path:
            return jsonify({"error": "Missing directory parameter"}), 400

        media_dir = resolve_media_path(directory_path)
        # Ensure media_dir is a Path object
        if not isinstance(media_dir, Path):
            media_dir = Path(media_dir)
        
        # Check if this is the state/media directory and MySQL backend is enabled
        # Always use MySQL for state/media
        is_state_media = is_state_media_directory(media_dir)
        state_media_path = get_state_media_path()
        
        if is_state_media and state_media_path:
            # Delete from MySQL and also delete the media file from disk
            from db import media_metadata
            record = media_metadata.load_media_metadata(unique_id)
            if not record:
                return jsonify({"error": "Media record not found"}), 404
            
            # Delete the media file from disk (similar to DirectoryMediaSource.delete_record)
            media_file_name = record.get("media_file")
            if media_file_name:
                media_path = state_media_path / media_file_name
                if media_path.exists():
                    media_path.unlink()
                    logger.debug(f"Deleted media file {media_file_name} from {state_media_path}")
            else:
                # Try common extensions if media_file field is not set
                for ext in MEDIA_FILE_EXTENSIONS:
                    media_path = state_media_path / f"{unique_id}{ext}"
                    if media_path.exists():
                        media_path.unlink()
                        logger.debug(f"Deleted media file {unique_id}{ext} from {state_media_path}")
                        break
            
            # Delete metadata from MySQL
            media_metadata.delete_media_metadata(unique_id)
        else:
            # Delete from filesystem
            source = get_directory_media_source(media_dir)
            record = source.get_cached_record(unique_id)
            if not record:
                return jsonify({"error": "Media record not found"}), 404
            source.delete_record(unique_id)

        logger.info(f"Deleted media {unique_id} from {directory_path}")
        return jsonify({"success": True})

    except Exception as e:
        logger.error(f"Error deleting media {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@media_bp.route("/api/download/<unique_id>", methods=["POST"])
def api_download_media(unique_id: str):
    """Download missing media file using Telegram API."""
    try:
        # For now, this is a placeholder since we need the original document reference
        # to download by unique_id. This would require a more complex lookup system.
        return (
            jsonify(
                {
                    "error": "Download by unique_id not yet implemented. Use sticker set import instead."
                }
            ),
            501,
        )

    except Exception as e:
        logger.error(f"Error downloading media {unique_id}: {e}")
        return jsonify({"error": str(e)}), 500


@media_bp.route("/api/import-sticker-set", methods=["POST"])
def api_import_sticker_set():
    """Import all stickers from a sticker set."""
    try:
        data = request.json
        sticker_set_name = data.get("sticker_set_name")
        target_directory = data.get("target_directory")

        logger.info(
            f"Flask route: Starting import for {sticker_set_name} to {target_directory}"
        )

        if not sticker_set_name or not target_directory:
            return (
                jsonify({"error": "Missing sticker_set_name or target_directory"}),
                400,
            )

        manager = get_puppet_master_manager()
        if not manager.is_configured:
            return (
                jsonify(
                    {
                        "error": "Puppet master phone is not configured. Set CINDY_PUPPET_MASTER_PHONE and log in with './telegram_login.sh --puppet-master'."
                    }
                ),
                503,
            )

        def _coro(client: TelegramClient):
            return import_sticker_set_async(client, sticker_set_name, target_directory)

        try:
            result = manager.run(_coro, timeout=300)
        except PuppetMasterNotConfigured:
            return (
                jsonify(
                    {
                        "error": "Puppet master is not configured. Set CINDY_PUPPET_MASTER_PHONE and log in with './telegram_login.sh --puppet-master'."
                    }
                ),
                503,
            )
        except PuppetMasterUnavailable as exc:
            logger.error("Puppet master unavailable during sticker import: %s", exc)
            return jsonify({"error": str(exc)}), 503
        except FuturesTimeoutError:
            logger.error("Sticker import timed out after 300 seconds.")
            return (
                jsonify(
                    {
                        "error": "Sticker import timed out after 300 seconds. Please try again."
                    }
                ),
                504,
            )

        logger.info("Flask route: async import completed successfully")
        return jsonify(result)

    except Exception as e:
        logger.error(f"Error importing sticker set: {e}")
        logger.error(f"Exception type: {type(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500


