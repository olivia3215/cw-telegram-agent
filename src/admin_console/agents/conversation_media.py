# admin_console/agents/conversation_media.py
#
# Conversation media serving routes for the admin console (emoji and media).

import asyncio
import glob
import logging
from pathlib import Path
from datetime import UTC

from flask import Blueprint, Response, jsonify  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name
from config import CONFIG_DIRECTORIES, STATE_DIRECTORY
from media.media_source import MediaStatus, get_default_media_source_chain
from media.media_sources import get_directory_media_source
from media.mime_utils import detect_mime_type_from_bytes, get_file_extension_from_mime_or_bytes, is_tgs_mime_type
from telegram_download import download_media_bytes
from telegram_media import iter_media_parts
from clock import clock
from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest, GetStickerSetRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import InputStickerSetID  # pyright: ignore[reportMissingImports]

logger = logging.getLogger(__name__)


def _escape_quoted_string_value(value: str) -> str:
    """
    Escape a value for use in a quoted-string within HTTP headers per RFC 6266.

    In quoted-string values, the following characters must be escaped:
    - Backslash `\\` -> `\\\\`
    - Double quote `"` -> `\\"`

    Args:
        value: The string value to escape

    Returns:
        The escaped string safe for use in quoted-string values
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def register_conversation_media_routes(agents_bp: Blueprint):
    """Register conversation media serving routes."""

    @agents_bp.route("/api/agents/<agent_config_name>/emoji/<document_id>", methods=["GET"])
    def api_get_custom_emoji(agent_config_name: str, document_id: str):
        """Serve custom emoji image by document ID, using media pipeline for caching and downloading."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                doc_id = int(document_id)
            except ValueError:
                return jsonify({"error": "Invalid document ID"}), 400

            async def _get_emoji():
                try:
                    # Use GetCustomEmojiDocumentsRequest to fetch the document by document_id
                    logger.debug(f"Fetching custom emoji document {doc_id} using GetCustomEmojiDocumentsRequest")
                    # Fetch the custom emoji document
                    result = await agent.client(GetCustomEmojiDocumentsRequest(document_id=[doc_id]))

                    if not result:
                        logger.warning(f"Custom emoji document {doc_id} - GetCustomEmojiDocumentsRequest returned None")
                        return None, None

                    # Check different possible result structures
                    documents = None
                    if hasattr(result, "documents"):
                        documents = result.documents
                    elif hasattr(result, "document"):
                        documents = [result.document] if result.document else []
                    elif isinstance(result, list):
                        documents = result

                    if not documents or len(documents) == 0:
                        logger.warning(f"Custom emoji document {doc_id} not found via GetCustomEmojiDocumentsRequest")
                        return None, None

                    # Get the first document (should only be one for a single document_id)
                    doc = documents[0] if documents else None
                    if not doc:
                        logger.warning(f"Custom emoji document {doc_id} returned empty result")
                        return None, None

                    # Get unique_id from document for use with media pipeline
                    from telegram_media import get_unique_id
                    unique_id = get_unique_id(doc)
                    if not unique_id:
                        logger.warning(f"Custom emoji document {doc_id} has no unique_id")
                        return None, None

                    logger.info(f"Custom emoji: document_id={doc_id}, unique_id={unique_id}")

                    # Extract sticker set information from document attributes
                    sticker_set_name = None
                    sticker_set_id = None
                    sticker_access_hash = None
                    sticker_name = None

                    attrs = getattr(doc, "attributes", None)
                    if isinstance(attrs, (list, tuple)):
                        for a in attrs:
                            # Check for DocumentAttributeSticker (regular sticker) or DocumentAttributeCustomEmoji
                            if hasattr(a, "stickerset"):
                                ss = getattr(a, "stickerset", None)
                                if ss:
                                    sticker_set_name = getattr(ss, "short_name", None)
                                    sticker_set_id = getattr(ss, "id", None)
                                    sticker_access_hash = getattr(ss, "access_hash", None)
                                # Get sticker name (emoji character)
                                sticker_name = getattr(a, "alt", None)

                    # Also check emoji directly on document
                    if not sticker_name:
                        sticker_name = getattr(doc, "emoji", None)

                    # If we have sticker_set_id but no short_name, query the set to get the name, title, and emoji status
                    sticker_set_title = None
                    is_emoji_set = None

                    if sticker_set_id and not sticker_set_name:
                        try:
                            logger.debug(f"Querying sticker set for custom emoji {doc_id}: set_id={sticker_set_id}")

                            sticker_set_result = await agent.client(
                                GetStickerSetRequest(
                                    stickerset=InputStickerSetID(
                                        id=sticker_set_id,
                                        access_hash=sticker_access_hash or 0
                                    ),
                                    hash=0
                                )
                            )

                            if sticker_set_result and hasattr(sticker_set_result, 'set'):
                                set_obj = sticker_set_result.set
                                sticker_set_name = getattr(set_obj, 'short_name', None)
                                sticker_set_title = getattr(set_obj, 'title', None)

                                # Check if this is an emoji set
                                if hasattr(set_obj, 'emojis') and getattr(set_obj, 'emojis', False):
                                    is_emoji_set = True
                                else:
                                    # Check set_type attribute if available
                                    set_type = getattr(set_obj, 'set_type', None)
                                    if set_type:
                                        type_str = str(set_type)
                                        if 'emoji' in type_str.lower() or 'Emoji' in type_str:
                                            is_emoji_set = True

                                if sticker_set_name:
                                    logger.debug(f"Got sticker set info for custom emoji {doc_id}: name={sticker_set_name}, title={sticker_set_title}, is_emoji_set={is_emoji_set}")
                        except Exception as e:
                            logger.debug(f"Failed to query sticker set for custom emoji {doc_id}: {e}")

                    # Use media pipeline to get/cache the emoji
                    # This will handle caching, downloading, and description generation
                    media_chain = get_default_media_source_chain()

                    logger.info(f"Calling media pipeline for custom emoji {doc_id}: unique_id={unique_id}, sticker_set={sticker_set_name}, is_emoji_set={is_emoji_set}, sticker_name={sticker_name}")

                    # Build metadata dict to pass additional fields
                    metadata = {}
                    if sticker_set_title is not None:
                        metadata['sticker_set_title'] = sticker_set_title
                    if is_emoji_set is not None:
                        metadata['is_emoji_set'] = is_emoji_set

                    record = await media_chain.get(
                        unique_id=unique_id,
                        agent=agent,
                        doc=doc,
                        kind="sticker",  # Custom emojis are treated as stickers
                        sender_id=None,
                        sender_name=None,
                        channel_id=None,
                        channel_name=None,
                        sticker_set_name=sticker_set_name,
                        sticker_set_id=sticker_set_id,
                        sticker_access_hash=sticker_access_hash,
                        sticker_name=sticker_name,
                        **metadata  # Pass additional metadata fields
                    )

                    if not record:
                        logger.warning(f"Custom emoji {doc_id} (unique_id: {unique_id}) not found via media pipeline")
                        return None, None

                    logger.info(f"Media pipeline returned record for custom emoji {doc_id}: status={record.get('status')}, description={record.get('description')[:50] if record.get('description') else None}")

                    # After calling media_chain.get(), the file should be cached
                    # Find the cached file using unique_id
                    cached_file = None
                    escaped_unique_id = glob.escape(unique_id)

                    # Check all config directories first (curated media)
                    for config_dir in CONFIG_DIRECTORIES:
                        config_media_dir = Path(config_dir) / "media"
                        if config_media_dir.exists() and config_media_dir.is_dir():
                            for file_path in config_media_dir.glob(f"{escaped_unique_id}.*"):
                                if file_path.suffix.lower() != ".json":
                                    cached_file = file_path
                                    break
                            if cached_file:
                                break

                    # If not found in config directories, check state/media/
                    if not cached_file:
                        state_media_dir = Path(STATE_DIRECTORY) / "media"
                        if state_media_dir.exists() and state_media_dir.is_dir():
                            for file_path in state_media_dir.glob(f"{escaped_unique_id}.*"):
                                if file_path.suffix.lower() != ".json":
                                    cached_file = file_path
                                    break

                    if not cached_file or not cached_file.exists():
                        logger.warning(f"Custom emoji {doc_id} (unique_id: {unique_id}) processed but cached file not found")
                        return None, None

                    # Read the cached file
                    with open(cached_file, "rb") as f:
                        emoji_bytes = f.read()

                    return emoji_bytes, unique_id
                except Exception as e:
                    logger.error(f"Error fetching custom emoji {doc_id}: {e}", exc_info=True)
                    return None, None

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                emoji_bytes, unique_id = agent.execute(_get_emoji(), timeout=10.0)
                if not emoji_bytes:
                    logger.warning(f"Custom emoji {document_id} not found or failed to download")
                    return jsonify({"error": "Emoji not found"}), 404

                # Detect MIME type
                mime_type = detect_mime_type_from_bytes(emoji_bytes)
                if not mime_type:
                    mime_type = "image/webp"  # Default for custom emojis

                # Check if it's an animated emoji (TGS/Lottie)
                is_animated = is_tgs_mime_type(mime_type)

                headers = {
                    "Cache-Control": "public, max-age=86400",  # Cache for 1 day
                }
                if is_animated:
                    headers["X-Emoji-Type"] = "animated"  # Signal to frontend that this needs Lottie

                return Response(
                    emoji_bytes,
                    mimetype=mime_type,
                    headers=headers
                )
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error fetching custom emoji: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout fetching custom emoji for agent {agent_config_name}, document {document_id}")
                return jsonify({"error": "Timeout fetching emoji"}), 504
            except Exception as e:
                logger.error(f"Error fetching custom emoji: {e}")
                return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"Error getting custom emoji for {agent_config_name}/{document_id}: {e}")
            return jsonify({"error": str(e)}), 500

    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>/media/<message_id>/<unique_id>", methods=["GET"])
    def api_get_conversation_media(agent_config_name: str, user_id: str, message_id: str, unique_id: str):
        """Serve media from a Telegram message, using cache if available."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            try:
                msg_id = int(message_id)
            except ValueError:
                return jsonify({"error": "Invalid message ID"}), 400

            # Resolve user_id (which may be a username) to channel_id
            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            # First, check if media is cached in any of the media directories
            # Check config directories first (curated media), then state/media/ (AI cache)
            # This matches the priority order of the media source chain
            cached_file = None

            # Escape unique_id to prevent glob pattern injection attacks
            escaped_unique_id = glob.escape(unique_id)

            # Check all config directories first (without fallback to state/media/)
            for config_dir in CONFIG_DIRECTORIES:
                config_media_dir = Path(config_dir) / "media"
                if config_media_dir.exists() and config_media_dir.is_dir():
                    for file_path in config_media_dir.glob(f"{escaped_unique_id}.*"):
                        if file_path.suffix.lower() != ".json":
                            cached_file = file_path
                            break
                if cached_file:
                    break

            # If not found in any config directory, check state/media/ directly
            if not cached_file:
                state_media_dir = Path(STATE_DIRECTORY) / "media"
                if state_media_dir.exists() and state_media_dir.is_dir():
                    for file_path in state_media_dir.glob(f"{escaped_unique_id}.*"):
                        if file_path.suffix.lower() != ".json":
                            cached_file = file_path
                            break

            # If found in cache, serve from cache
            if cached_file and cached_file.exists():
                try:
                    # Patch missing sticker_set_name in metadata when serving from state/media
                    # (e.g. records cached before resolution or when resolution failed).
                    # Fire-and-forget: do not block the response on Telegram API calls.
                    state_media_dir = Path(STATE_DIRECTORY) / "media"
                    if str(cached_file.resolve()).startswith(
                        str(state_media_dir.resolve())
                    ):
                        try:
                            from db import media_metadata

                            record = media_metadata.load_media_metadata(unique_id)
                            if record and record.get("kind") in (
                                "sticker",
                                "animated_sticker",
                            ):
                                needs_patch = not record.get("sticker_set_name")
                                if needs_patch and agent.client and agent.client.is_connected():
                                    try:
                                        client_loop = agent._get_client_loop()
                                        if client_loop and client_loop.is_running():

                                            async def _patch_sticker_metadata():
                                                entity = await agent.client.get_entity(
                                                    channel_id
                                                )
                                                message = await agent.client.get_messages(
                                                    entity, ids=int(message_id)
                                                )
                                                if isinstance(message, list):
                                                    message = message[0] if message else None
                                                if message:
                                                    for item in iter_media_parts(message):
                                                        if item.unique_id == unique_id and item.is_sticker():
                                                            from media.media_injector import (
                                                                _maybe_get_sticker_set_metadata,
                                                            )

                                                            name, title = (
                                                                await _maybe_get_sticker_set_metadata(
                                                                    agent, item
                                                                )
                                                            )
                                                            if name:
                                                                media_metadata.update_sticker_set_metadata(
                                                                    unique_id, name, title
                                                                )
                                                            return
                                                return None

                                            def _log_patch_exception(fut):
                                                try:
                                                    fut.result()
                                                except Exception as e:
                                                    logger.debug(
                                                        f"Background sticker metadata patch failed for {unique_id}: {e}"
                                                    )

                                            fut = asyncio.run_coroutine_threadsafe(
                                                _patch_sticker_metadata(), client_loop
                                            )
                                            fut.add_done_callback(_log_patch_exception)
                                    except Exception as e:
                                        logger.debug(
                                            f"Could not schedule sticker metadata patch for {unique_id}: {e}"
                                        )
                        except Exception as e:
                            logger.debug(
                                f"Could not check/patch sticker metadata for {unique_id}: {e}"
                            )

                    # Read the cached file
                    with open(cached_file, "rb") as f:
                        media_bytes = f.read()

                    # Detect MIME type
                    mime_type = detect_mime_type_from_bytes(media_bytes[:1024])

                    logger.debug(
                        f"Serving cached media {unique_id} from {cached_file} for {agent_config_name}/{user_id}/{message_id}"
                    )

                    # Try to get filename from metadata JSON if it exists
                    file_name = None
                    metadata_file = cached_file.with_suffix(".json")
                    if metadata_file.exists():
                        try:
                            import json
                            with open(metadata_file, "r") as f:
                                metadata = json.load(f)
                                # Extract filename from metadata if stored
                                file_name = metadata.get("file_name")
                        except Exception:
                            pass

                    # Use unique_id as fallback if filename not found in metadata
                    import urllib.parse
                    if file_name:
                        # Escape filename for RFC 6266 quoted-string (escape backslashes and double quotes)
                        escaped_filename = _escape_quoted_string_value(file_name)
                        encoded_filename = urllib.parse.quote(file_name, safe='')
                        content_disposition = f"inline; filename=\"{escaped_filename}\"; filename*=UTF-8''{encoded_filename}"
                    else:
                        content_disposition = f"inline; filename={unique_id}"

                    return Response(
                        media_bytes,
                        mimetype=mime_type or "application/octet-stream",
                        headers={"Content-Disposition": content_disposition}
                    )
                except Exception as e:
                    logger.warning(f"Error reading cached media file {cached_file}: {e}, falling back to Telegram download")
                    # Fall through to download from Telegram

            # Not in cache, or cache read failed - download from Telegram
            if not agent.client or not agent.client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            # Check if agent's event loop is accessible
            try:
                client_loop = agent._get_client_loop()
                if not client_loop or not client_loop.is_running():
                    raise RuntimeError("Agent client event loop is not accessible or not running")
            except Exception as e:
                logger.warning(f"Cannot fetch media - event loop check failed: {e}")
                return jsonify({"error": "Agent client event loop is not available"}), 503

            # This is async, so we need to run it in the client's event loop
            async def _get_media():
                try:
                    client = agent.client
                    entity = await client.get_entity(channel_id)

                    # Get the message
                    message = await client.get_messages(entity, ids=msg_id)
                    if not message:
                        return None, None, None

                    # Handle case where get_messages returns a list
                    if isinstance(message, list):
                        if len(message) == 0:
                            return None, None, None
                        message = message[0]

                    # Find the media item with matching unique_id
                    media_items = iter_media_parts(message)
                    for item in media_items:
                        if item.unique_id == unique_id:
                            # Resolve sticker set metadata when missing (e.g. InputStickerSetID has no short_name)
                            # so we can persist it when caching and display under proper set name in Media Library
                            if item.is_sticker() and not getattr(item, "sticker_set_name", None):
                                try:
                                    from media.media_injector import _maybe_get_sticker_set_metadata
                                    resolved_name, resolved_title = await _maybe_get_sticker_set_metadata(
                                        agent, item
                                    )
                                    if resolved_name:
                                        item.sticker_set_name = resolved_name
                                    if resolved_title:
                                        item.sticker_set_title = resolved_title
                                except Exception as e:
                                    logger.debug(
                                        f"Could not resolve sticker set for {unique_id}: {e}"
                                    )
                            # Download media bytes
                            media_bytes = await download_media_bytes(client, item.file_ref)
                            # Detect MIME type
                            mime_type = detect_mime_type_from_bytes(media_bytes[:1024])

                            # Extract filename from document for documents
                            file_name = None
                            if hasattr(item, "kind") and hasattr(item.kind, "value"):
                                if item.kind.value == "document" and item.file_ref:
                                    # Try to get filename from document.file_name first
                                    file_name = getattr(item.file_ref, "file_name", None)
                                    if not file_name:
                                        # Check attributes for DocumentAttributeFilename
                                        attrs = getattr(item.file_ref, "attributes", None)
                                        if isinstance(attrs, (list, tuple)):
                                            for attr in attrs:
                                                # Check if this is DocumentAttributeFilename
                                                if hasattr(attr, "file_name"):
                                                    file_name = getattr(attr, "file_name", None)
                                                    if file_name:
                                                        break
                                                # Also check by class name as fallback
                                                attr_class = getattr(attr, "__class__", None)
                                                if attr_class and hasattr(attr_class, "__name__"):
                                                    if attr_class.__name__ == "DocumentAttributeFilename":
                                                        file_name = getattr(attr, "file_name", None)
                                                        if file_name:
                                                            break

                            return media_bytes, mime_type, item, file_name

                    return None, None, None, None
                except Exception as e:
                    logger.error(f"Error fetching media: {e}")
                    return None, None, None, None

            # Use agent.execute() to run the coroutine on the agent's event loop
            try:
                media_bytes, mime_type, media_item, file_name = agent.execute(_get_media(), timeout=30.0)
                if media_bytes is None:
                    return jsonify({"error": "Media not found"}), 404

                logger.debug(
                    f"Downloaded media {unique_id} from Telegram for {agent_config_name}/{user_id}/{message_id}"
                )

                # Cache the downloaded media file to state/media/ for future use
                # Use the same storage mechanism as the normal media source chain
                try:
                    # Get file extension from MIME type or by detecting from bytes
                    file_extension = get_file_extension_from_mime_or_bytes(mime_type, media_bytes)

                    # Store media file if we have an extension
                    if file_extension:
                        # MySQL is required - use MySQLMediaSource with directory_source for media files
                        state_media_dir = Path(STATE_DIRECTORY) / "media"
                        from media.mysql_media_source import MySQLMediaSource
                        # Use MySQL for metadata, DirectoryMediaSource for media files on disk
                        directory_source = get_directory_media_source(state_media_dir)
                        cache_source = MySQLMediaSource(directory_source=directory_source)

                        # Check if file already exists to avoid overwriting
                        media_filename = f"{unique_id}{file_extension}"
                        media_file = state_media_dir / media_filename
                        if not media_file.exists():
                            # Create a proper record with full metadata from MediaItem
                            record = {
                                "unique_id": unique_id,
                                "description": None,
                                "status": MediaStatus.TEMPORARY_FAILURE.value,
                                "failure_reason": "Downloaded from admin console, description pending",
                                "ts": clock.now(UTC).isoformat(),
                            }

                            # Add full metadata from MediaItem if available
                            if media_item:
                                # Add kind (required for proper classification)
                                if hasattr(media_item.kind, "value"):
                                    record["kind"] = media_item.kind.value
                                else:
                                    record["kind"] = str(media_item.kind)

                                # Add sticker-specific metadata
                                if media_item.sticker_set_name:
                                    record["sticker_set_name"] = media_item.sticker_set_name
                                if hasattr(media_item, "sticker_set_title") and media_item.sticker_set_title:
                                    record["sticker_set_title"] = media_item.sticker_set_title
                                if media_item.sticker_name:
                                    record["sticker_name"] = media_item.sticker_name
                                if media_item.sticker_set_id:
                                    record["sticker_set_id"] = media_item.sticker_set_id
                                if media_item.sticker_access_hash:
                                    record["sticker_access_hash"] = media_item.sticker_access_hash

                                # Add duration for videos/animations
                                if media_item.duration:
                                    record["duration"] = media_item.duration

                            # Add MIME type
                            if mime_type:
                                record["mime_type"] = mime_type

                            # Add filename for documents
                            if file_name:
                                record["file_name"] = file_name

                            try:
                                # MySQLMediaSource.put is async; ensure it is executed so we don't
                                # drop an un-awaited coroutine (which would skip caching and emit
                                # "coroutine was never awaited" warnings).
                                asyncio.run(
                                    cache_source.put(
                                        unique_id,
                                        record,
                                        media_bytes,
                                        file_extension,
                                        agent=agent,
                                    )
                                )
                                logger.debug(
                                    f"Cached media file {media_filename} with full metadata to {state_media_dir} for {unique_id}"
                                )
                            except Exception as e:
                                logger.warning(f"Failed to cache media file {media_filename}: {e}")
                    else:
                        logger.debug(f"Could not determine file extension for {unique_id}, skipping cache")
                except Exception as e:
                    # Don't fail the request if caching fails
                    logger.warning(f"Error caching media file for {unique_id}: {e}")

                # Use filename if available, otherwise fall back to unique_id
                # Properly escape filename for Content-Disposition header
                import urllib.parse
                if file_name:
                    # Escape filename for RFC 6266 quoted-string (escape backslashes and double quotes)
                    escaped_filename = _escape_quoted_string_value(file_name)
                    # URL-encode the filename for the Content-Disposition header
                    # Use RFC 5987 format for better compatibility
                    encoded_filename = urllib.parse.quote(file_name, safe='')
                    content_disposition = f"inline; filename=\"{escaped_filename}\"; filename*=UTF-8''{encoded_filename}"
                else:
                    content_disposition = f"inline; filename={unique_id}"

                return Response(
                    media_bytes,
                    mimetype=mime_type or "application/octet-stream",
                    headers={"Content-Disposition": content_disposition}
                )
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error fetching media: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout fetching media for agent {agent_config_name}, message {message_id}")
                return jsonify({"error": "Timeout fetching media"}), 504
            except Exception as e:
                logger.error(f"Error fetching media: {e}")
                return jsonify({"error": str(e)}), 500
        except Exception as e:
            logger.error(f"Error getting media for {agent_config_name}/{user_id}/{message_id}/{unique_id}: {e}")
            return jsonify({"error": str(e)}), 500
