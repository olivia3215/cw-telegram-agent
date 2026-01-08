# admin_console/media.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

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
from config import MEDIA_DESC_BUDGET_PER_TICK, STATE_DIRECTORY
from datetime import UTC
from admin_console.helpers import (
    add_cache_busting_headers,
    find_media_file,
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
from media.mime_utils import detect_mime_type_from_bytes, is_tgs_mime_type
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
    """Get list of media files in a directory."""
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
        state_media_path = Path(STATE_DIRECTORY) / "media"
        if not isinstance(state_media_path, Path):
            state_media_path = Path(state_media_path)
        is_state_media = str(media_dir.resolve()) == str(state_media_path.resolve())

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

        # Get optional limit parameter
        limit_str = request.args.get("limit", "").strip()
        limit = None
        if limit_str:
            try:
                limit = int(limit_str)
                if limit < 1:
                    limit = None
            except ValueError:
                limit = None

        media_files = []

        # Create a single event loop for all async operations in this request
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Get unique IDs - from MySQL for state/media, otherwise from JSON files
            unique_ids = []
            use_mysql = is_state_media
            if is_state_media:
                # For MySQL, query the database directly to get all unique_ids
                try:
                    from db.connection import get_db_connection
                    with get_db_connection() as conn:
                        cursor = conn.cursor()
                        try:
                            cursor.execute("SELECT unique_id FROM media_metadata")
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
                    # Fall back to filesystem
                    unique_ids = [json_file.stem for json_file in media_dir.glob("*.json")]
            else:
                # Find all JSON files to get unique IDs (filesystem)
                unique_ids = [json_file.stem for json_file in media_dir.glob("*.json")]
            
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

                    # Look for associated media file
                    media_file_path = find_media_file(media_dir, unique_id)
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
                    elif (
                        mime_type == "application/gzip"
                        and media_file_path
                        and media_file_path.suffix.lower() == ".tgs"
                    ):
                        mime_type = "application/x-tgsticker"

                    # Group by sticker set for organization
                    kind = record.get("kind", "unknown")
                    if is_tgs_mime_type(mime_type) and kind == "sticker":
                        kind = "animated_sticker"

                    # Extract sticker_name early so we can use it for emoji set detection
                    sticker_name = record.get("sticker_name", "")

                    if kind == "sticker" or kind == "animated_sticker":
                        sticker_set = record.get("sticker_set_name")
                        sticker_set_title = record.get("sticker_set_title")  # May be None for old records
                        
                        # If sticker has no set name, treat it as regular media based on type
                        if not sticker_set:
                            # Unnamed stickers are treated as images or videos
                            if kind == "animated_sticker" or is_tgs_mime_type(mime_type):
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
                    
                    media_files.append(
                        {
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
                    )

                except Exception as e:
                    logger.error(f"Error processing {unique_id}: {e}")
                    continue
        finally:
            loop.close()

        # Apply limit if specified and directory is state/media
        if limit is not None and is_state_media:
            # Sort by file creation time (most recent first), then filter to limit
            # Items without file_creation_time go to the end
            media_files.sort(
                key=lambda x: x.get("_file_creation_time") or 0,
                reverse=True
            )
            # Take only the first 'limit' items
            media_files = media_files[:limit]

        # Remove internal sorting field before returning
        for media in media_files:
            media.pop("_file_creation_time", None)

        # Group by sticker set
        grouped_media = {}
        for media in media_files:
            sticker_set = media["sticker_set_name"]
            if sticker_set not in grouped_media:
                grouped_media[sticker_set] = []
            grouped_media[sticker_set].append(media)

        response = jsonify(
            {
                "media_files": media_files,
                "grouped_media": grouped_media,
                "directory": directory_path,
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

        # Find the media file
        media_file = find_media_file(media_dir, unique_id)
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
        state_media_path = Path(STATE_DIRECTORY) / "media"
        if not isinstance(state_media_path, Path):
            state_media_path = Path(state_media_path)
        is_state_media = str(media_dir.resolve()) == str(state_media_path.resolve())
        
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
        is_state_media = str(media_dir.resolve()) == str((Path(STATE_DIRECTORY) / "media").resolve())
        
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
        
        # Find the media file
        media_file = find_media_file(media_dir, unique_id)
        if not media_file:
            return jsonify({"error": "Media file not found"}), 404

        # Reset the media description budget for this refresh request
        # When user explicitly requests refresh, they should be able to get a description
        # regardless of the current budget state
        reset_description_budget(MEDIA_DESC_BUDGET_PER_TICK)
        logger.info(
            f"Refresh-from-AI: reset budget to {MEDIA_DESC_BUDGET_PER_TICK} for {unique_id}"
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
            from media.mime_utils import get_mime_type_from_file_extension
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
        state_media_path = Path(STATE_DIRECTORY) / "media"
        if not isinstance(state_media_path, Path):
            state_media_path = Path(state_media_path)
        is_from_state_media = str(from_dir.resolve()) == str(state_media_path.resolve())
        is_to_state_media = str(to_dir.resolve()) == str(state_media_path.resolve())

        # Handle no-op case (moving state/media to itself)
        if is_from_state_media and is_to_state_media:
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

            # Move media file from state/media to destination
            media_file_name = record.get("media_file")
            if media_file_name:
                source_media = from_dir / media_file_name
                if source_media.exists():
                    to_dir.mkdir(parents=True, exist_ok=True)
                    target_media = to_dir / media_file_name
                    source_media.replace(target_media)
                    logger.debug(f"Moved media file {media_file_name} from {from_dir} to {to_dir}")
            else:
                # Try common extensions if media_file field is not set
                moved = False
                for ext in MEDIA_FILE_EXTENSIONS:
                    source_media = from_dir / f"{unique_id}{ext}"
                    if source_media.exists():
                        to_dir.mkdir(parents=True, exist_ok=True)
                        target_media = to_dir / source_media.name
                        source_media.replace(target_media)
                        media_file_name = source_media.name
                        record["media_file"] = media_file_name
                        logger.debug(f"Moved media file {source_media.name} from {from_dir} to {to_dir}")
                        moved = True
                        break
                if not moved:
                    logger.warning(f"No media file found for {unique_id} in {from_dir}")

            # Write JSON file to destination directory
            # Filter the record to exclude MySQL-specific or state-specific fields
            # (same filtering as DirectoryMediaSource does for config directories)
            # Only delete from MySQL after successful write to prevent data loss
            try:
                to_source.put(unique_id, record)
            except Exception as e:
                logger.error(f"Failed to write media {unique_id} to {to_directory}: {e}")
                return jsonify({"error": f"Failed to write to destination: {str(e)}"}), 500

            # Delete from MySQL only after successful write
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

            # Move media file from source to state/media
            media_file_name = record.get("media_file")
            if media_file_name:
                source_media = from_dir / media_file_name
                if source_media.exists():
                    to_dir.mkdir(parents=True, exist_ok=True)
                    target_media = to_dir / media_file_name
                    source_media.replace(target_media)
                    logger.debug(f"Moved media file {media_file_name} from {from_dir} to {to_dir}")
            else:
                # Try common extensions if media_file field is not set
                moved = False
                for ext in MEDIA_FILE_EXTENSIONS:
                    source_media = from_dir / f"{unique_id}{ext}"
                    if source_media.exists():
                        to_dir.mkdir(parents=True, exist_ok=True)
                        target_media = to_dir / source_media.name
                        source_media.replace(target_media)
                        media_file_name = source_media.name
                        record["media_file"] = media_file_name
                        logger.debug(f"Moved media file {source_media.name} from {from_dir} to {to_dir}")
                        moved = True
                        break
                if not moved:
                    logger.warning(f"No media file found for {unique_id} in {from_dir}")

            # Save to MySQL (filters fields automatically)
            from db import media_metadata
            media_metadata.save_media_metadata(record)

            # Delete from source directory
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
        state_media_path = Path(STATE_DIRECTORY) / "media"
        if not isinstance(state_media_path, Path):
            state_media_path = Path(state_media_path)
        is_state_media = str(media_dir.resolve()) == str(state_media_path.resolve())
        
        if is_state_media:
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


