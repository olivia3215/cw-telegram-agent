# admin_console/agents/conversation_download.py
#
# Download conversation route for exporting conversations as zip files.

import copy
import glob
import gzip
import html
import io
import json as json_lib
import logging
import mimetypes
import re
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from flask import Blueprint, Response, jsonify, request  # pyright: ignore[reportMissingImports]

from admin_console.helpers import get_agent_by_name, get_state_media_path
from config import CONFIG_DIRECTORIES
from llm.factory import create_llm_from_name

# Import markdown_to_html and placeholder functions from conversation module
from admin_console.agents.conversation import (
    markdown_to_html,
    replace_html_tags_with_placeholders,
    restore_html_tags_from_placeholders,
)

# Import emoji replacement functions from conversation_get module
from admin_console.agents.conversation_get import (
    _replace_custom_emojis_with_images,
    _replace_custom_emoji_in_reactions,
)

logger = logging.getLogger(__name__)

# Translation JSON schema for message translation
_TRANSLATION_SCHEMA = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "string",
                        "description": "The message ID from the input"
                    },
                    "translated_text": {
                        "type": "string",
                        "description": "The English translation of the message text"
                    }
                },
                "required": ["message_id", "translated_text"],
                "additionalProperties": False
            }
        }
    },
    "required": ["translations"],
    "additionalProperties": False
}


def _cache_media_to_state(unique_id: str, filename: str, media_bytes: bytes) -> None:
    """Cache downloaded media to state/media for future conversation downloads."""
    try:
        state_media_dir = get_state_media_path()
        if state_media_dir is None:
            return
        state_media_dir.mkdir(parents=True, exist_ok=True)
        cache_path = state_media_dir / filename
        if not cache_path.exists():
            cache_path.write_bytes(media_bytes)
            logger.debug(f"Cached media {unique_id} to {cache_path} for future downloads")
    except Exception as e:
        logger.warning(f"Failed to cache media {unique_id} to state: {e}")


def register_conversation_download_routes(agents_bp: Blueprint):
    """Register conversation download route."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/conversation/<user_id>/download", methods=["POST"])
    def api_download_conversation(agent_config_name: str, user_id: str):
        """Download full conversation (up to 2500 messages) as a zip file with standalone HTML."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404

            # Get include_translations from request body
            data = request.json or {}
            include_translations = data.get("include_translations", False)

            if not agent.client or not agent.client.is_connected():
                return jsonify({"error": "Agent client not connected"}), 503

            # Resolve user_id to channel_id
            from admin_console.helpers import resolve_user_id_and_handle_errors
            channel_id, error_response = resolve_user_id_and_handle_errors(agent, user_id, logger)
            if error_response:
                return error_response

            # Check if agent's event loop is accessible
            try:
                client_loop = agent._get_client_loop()
                if not client_loop or not client_loop.is_running():
                    raise RuntimeError("Agent client event loop is not accessible or not running")
            except Exception as e:
                logger.warning(f"Cannot download conversation - event loop check failed: {e}")
                return jsonify({"error": "Agent client event loop is not available"}), 503

            # Import necessary modules for message fetching and formatting
            # Note: _replace_custom_emojis_with_images and _replace_custom_emoji_in_reactions
            # are already imported at module level using importlib
            from handlers.received_helpers.message_processing import format_message_reactions
            from media.media_injector import format_message_for_prompt, inject_media_descriptions
            from media.media_source import get_default_media_source_chain
            from utils.telegram import get_channel_name, is_dm
            from telegram_download import download_media_bytes
            from telegram_media import iter_media_parts, get_unique_id
            from media.mime_utils import detect_mime_type_from_bytes, get_file_extension_from_mime_or_bytes

            # This is async, so we need to run it in the client's event loop
            async def _download_conversation():
                try:
                    client = agent.client
                    # Use agent.get_cached_entity() to benefit from contacts fallback
                    entity = await agent.get_cached_entity(channel_id)
                    if not entity:
                        raise ValueError(f"Cannot resolve entity for channel_id {channel_id}")

                    # Fetch ALL messages (up to 2500) - not just unsummarized
                    # Don't use min_id filter - we want everything
                    messages = []
                    async for message in client.iter_messages(entity, limit=2500):
                        # Extract message data similar to conversation_get.py
                        msg_id = int(message.id)
                        
                        # Get sender info
                        from_id = getattr(message, "from_id", None)
                        sender_id = None
                        if from_id:
                            sender_id = getattr(from_id, "user_id", None) or getattr(from_id, "channel_id", None)
                        
                        if not sender_id:
                            sender = getattr(message, "sender", None)
                            if sender:
                                sender_id = getattr(sender, "id", None)
                        
                        is_from_agent = sender_id == agent.agent_id
                        
                        sender_name = None
                        if sender_id and isinstance(sender_id, int):
                            try:
                                sender_name = await get_channel_name(agent, sender_id)
                                if not sender_name or not sender_name.strip():
                                    sender_name = "User"
                            except Exception:
                                sender_name = "User"
                        elif sender_id:
                            sender_name = "User"
                        else:
                            sender_name = "User"
                        
                        timestamp = message.date.isoformat() if hasattr(message, "date") and message.date else None
                        
                        # Extract reply_to
                        reply_to_msg_id = None
                        reply_to = getattr(message, "reply_to", None)
                        if reply_to:
                            reply_to_msg_id_val = getattr(reply_to, "reply_to_msg_id", None)
                            if reply_to_msg_id_val is not None:
                                reply_to_msg_id = str(reply_to_msg_id_val)
                        
                        # Format reactions
                        reactions_str = await format_message_reactions(agent, message)
                        if reactions_str:
                            reactions_str = await _replace_custom_emoji_in_reactions(
                                reactions_str, agent_config_name, str(message.id), message, agent
                            )
                        
                        # Format media/stickers
                        media_chain = get_default_media_source_chain()
                        message_parts = await format_message_for_prompt(message, agent=agent, media_chain=media_chain)
                        
                        # Get text with formatting
                        text_markdown = getattr(message, "text_markdown", None)
                        raw_text = getattr(message, "message", None) or getattr(message, "text", None) or ""
                        entities = getattr(message, "entities", None) or []
                        
                        if not text_markdown or text_markdown == raw_text:
                            if raw_text and entities:
                                from utils.telegram_entities import entities_to_markdown
                                text_markdown = entities_to_markdown(raw_text, entities)
                            else:
                                text_markdown = raw_text
                        
                        # Convert markdown to HTML
                        text = markdown_to_html(text_markdown)
                        
                        # Replace custom emojis with images
                        text = await _replace_custom_emojis_with_images(
                            text, raw_text, entities, agent_config_name, str(message.id), message
                        )
                        
                        # Build message parts
                        parts = []
                        for part in message_parts:
                            if part.get("kind") == "text":
                                part_text = part.get("text", "")
                                part_html = markdown_to_html(part_text)
                                part_html = await _replace_custom_emojis_with_images(
                                    part_html, raw_text, entities, agent_config_name, str(message.id), message
                                )
                                parts.append({
                                    "kind": "text",
                                    "text": part_html
                                })
                            elif part.get("kind") == "media":
                                parts.append({
                                    "kind": "media",
                                    "media_kind": part.get("media_kind"),
                                    "rendered_text": part.get("rendered_text", ""),
                                    "unique_id": part.get("unique_id"),
                                    "sticker_set_name": part.get("sticker_set_name"),
                                    "sticker_name": part.get("sticker_name"),
                                    "is_animated": part.get("is_animated", False),
                                    "mime_type": part.get("mime_type"),
                                    "message_id": str(message.id),
                                })
                        
                        # If text is empty but we have parts, extract from first text part
                        if not text and parts:
                            for part in parts:
                                if part.get("kind") == "text":
                                    part_text = part.get("text", "")
                                    if part_text:
                                        text = part_text
                                        break
                        
                        # Ensure message has content
                        if not parts and not text:
                            parts.append({
                                "kind": "text",
                                "text": "[Message]"
                            })
                            text = "[Message]"
                        
                        messages.append({
                            "id": str(message.id),
                            "text": text,
                            "parts": parts,
                            "sender_id": str(sender_id) if sender_id else None,
                            "sender_name": sender_name,
                            "is_from_agent": is_from_agent,
                            "timestamp": timestamp,
                            "reply_to_msg_id": reply_to_msg_id,
                            "reactions": reactions_str,
                        })
                    
                    # Reverse to chronological order
                    messages = list(reversed(messages))
                    
                    logger.info(
                        f"[{agent_config_name}] Fetched {len(messages)} messages for download (channel {channel_id})"
                    )
                    
                    # Fetch translations if requested
                    translations = {}
                    if include_translations:
                        # Get messages with text that need translation
                        messages_to_translate = []
                        for msg in messages:
                            msg_id = str(msg.get("id", ""))
                            msg_text = msg.get("text", "")
                            if not msg_text:
                                continue
                            
                            # Check cache first
                            from translation_cache import get_translation
                            cached_translation = get_translation(msg_text)
                            if cached_translation:
                                translations[msg_id] = cached_translation
                            else:
                                messages_to_translate.append({
                                    "message_id": msg_id,
                                    "text": msg_text
                                })
                        
                        # Translate remaining messages in batches
                        if messages_to_translate:
                            from config import TRANSLATION_MODEL
                            if not TRANSLATION_MODEL:
                                logger.warning("TRANSLATION_MODEL not set, skipping translations")
                            else:
                                logger.info(
                                    "Conversation download translation using model: %s",
                                    TRANSLATION_MODEL,
                                )
                                translation_llm = create_llm_from_name(TRANSLATION_MODEL)
                                batch_size = 10
                                batches = [
                                    messages_to_translate[i:i + batch_size]
                                    for i in range(0, len(messages_to_translate), batch_size)
                                ]
                                
                                for batch in batches:
                                    # Create mapping from message_id to original text for saving to cache
                                    message_id_to_original_text = {
                                        msg["message_id"]: msg["text"]
                                        for msg in batch
                                    }
                                    
                                    # Replace HTML tags with placeholders
                                    batch_with_placeholders = []
                                    batch_tag_maps = {}
                                    
                                    for msg in batch:
                                        message_id = msg["message_id"]
                                        html_text = msg["text"]
                                        text_with_placeholders, tag_map = replace_html_tags_with_placeholders(html_text)
                                        batch_tag_maps[message_id] = tag_map
                                        batch_with_placeholders.append({
                                            "message_id": message_id,
                                            "text": text_with_placeholders
                                        })
                                    
                                    # Translate batch
                                    messages_json = json_lib.dumps(batch_with_placeholders, ensure_ascii=False, indent=2)
                                    translation_prompt = (
                                        "Translate the conversation messages into English.\n"
                                        "Preserve the message structure and return a JSON object with translations.\n"
                                        "\n"
                                        "Return a JSON object with this structure:\n"
                                        "{\n"
                                        "  \"translations\": [\n"
                                        "    {\"message_id\": \"123\", \"translated_text\": \"English translation here\"},\n"
                                        "    ...\n"
                                        "  ]\n"
                                        "}\n"
                                        "\n"
                                        "Translate all messages provided, maintaining the order and message IDs. "
                                        "The messages contain placeholder tags like <HTMLTAG1>, <HTMLTAG2>, etc. "
                                        "Do NOT modify these placeholders - preserve them exactly as they appear. "
                                        "Translate only the text content between placeholders. Ensure all JSON is properly formatted."
                                        "\n"
                                        "Input messages (as JSON, with placeholder tags):\n"
                                        f"{messages_json}\n"
                                    )
                                    
                                    system_prompt = (
                                        "You are a translation assistant. Translate messages into English and return JSON.\n\n"
                                        f"{translation_prompt}"
                                    )
                                    
                                    result_text = await translation_llm.query_with_json_schema(
                                        system_prompt=system_prompt,
                                        json_schema=copy.deepcopy(_TRANSLATION_SCHEMA),
                                        model=None,
                                        timeout_s=None,
                                    )
                                    
                                    if result_text:
                                        try:
                                            result = json_lib.loads(result_text)
                                            batch_translations = result.get("translations", [])
                                            
                                            # Save translations to cache and store in translations dict
                                            from translation_cache import save_translation
                                            
                                            for translation in batch_translations:
                                                message_id = translation.get("message_id")
                                                translated_text = translation.get("translated_text", "")
                                                if message_id and message_id in batch_tag_maps:
                                                    tag_map = batch_tag_maps[message_id]
                                                    restored_text = restore_html_tags_from_placeholders(
                                                        translated_text, tag_map
                                                    )
                                                    translations[message_id] = restored_text
                                                    
                                                    # Save to cache to avoid re-translating in future downloads
                                                    original_text = message_id_to_original_text.get(message_id)
                                                    if original_text:
                                                        try:
                                                            save_translation(original_text, restored_text)
                                                        except Exception as save_error:
                                                            logger.warning(f"Failed to save translation to cache for message {message_id}: {save_error}")
                                        except Exception as e:
                                            logger.warning(f"Error parsing translation batch: {e}")
                    
                    # Create temp directory for zip contents
                    with tempfile.TemporaryDirectory() as temp_dir:
                        temp_path = Path(temp_dir)
                        media_dir = temp_path / "media"
                        media_dir.mkdir()
                        
                        # Download all media files
                        media_map = {}  # unique_id -> filename
                        mime_map = {}  # unique_id -> mime_type
                        for msg in messages:
                            for part in msg.get("parts", []):
                                if part.get("kind") == "media":
                                    unique_id = part.get("unique_id")
                                    message_id = part.get("message_id")
                                    if not unique_id or unique_id in media_map:
                                        continue
                                    
                                    # Try to find cached media first
                                    cached_file = None
                                    escaped_unique_id = glob.escape(unique_id)
                                    
                                    for config_dir in CONFIG_DIRECTORIES:
                                        config_media_dir = Path(config_dir) / "media"
                                        if config_media_dir.exists():
                                            for file_path in config_media_dir.glob(f"{escaped_unique_id}.*"):
                                                if file_path.suffix.lower() != ".json":
                                                    cached_file = file_path
                                                    break
                                            if cached_file:
                                                break
                                    
                                    if not cached_file:
                                        state_media_dir = get_state_media_path()
                                        if state_media_dir is not None and state_media_dir.exists():
                                            for file_path in state_media_dir.glob(f"{escaped_unique_id}.*"):
                                                if file_path.suffix.lower() != ".json":
                                                    cached_file = file_path
                                                    break
                                    
                                    # If found in cache, copy it
                                    if cached_file and cached_file.exists():
                                        try:
                                            with open(cached_file, "rb") as f:
                                                media_bytes = f.read()
                                            mime_type = detect_mime_type_from_bytes(media_bytes[:1024])
                                            ext = get_file_extension_from_mime_or_bytes(mime_type, media_bytes)
                                            filename = f"{unique_id}{ext}"
                                            media_path = media_dir / filename
                                            with open(media_path, "wb") as f:
                                                f.write(media_bytes)
                                            media_map[unique_id] = filename
                                            mime_map[unique_id] = mime_type
                                        except Exception as e:
                                            logger.warning(f"Error copying cached media {unique_id}: {e}")
                                    else:
                                        # Download from Telegram
                                        try:
                                            msg_id_int = int(message_id)
                                            msg_obj = await client.get_messages(entity, ids=msg_id_int)
                                            if isinstance(msg_obj, list):
                                                if len(msg_obj) > 0:
                                                    msg_obj = msg_obj[0]
                                                else:
                                                    continue
                                            
                                            # Check if message is None (deleted or inaccessible)
                                            if msg_obj is None:
                                                logger.warning(f"Message {msg_id_int} not found (deleted or inaccessible) for media {unique_id}, skipping download")
                                                continue
                                            
                                            media_items = iter_media_parts(msg_obj)
                                            for item in media_items:
                                                if item.unique_id == unique_id:
                                                    media_bytes = await download_media_bytes(client, item.file_ref)
                                                    mime_type = detect_mime_type_from_bytes(media_bytes[:1024])
                                                    ext = get_file_extension_from_mime_or_bytes(mime_type, media_bytes)
                                                    filename = f"{unique_id}{ext}"
                                                    media_path = media_dir / filename
                                                    with open(media_path, "wb") as f:
                                                        f.write(media_bytes)
                                                    media_map[unique_id] = filename
                                                    mime_map[unique_id] = mime_type
                                                    # Cache to persistent storage for future downloads
                                                    _cache_media_to_state(unique_id, filename, media_bytes)
                                                    break
                                        except Exception as e:
                                            logger.warning(f"Error downloading media {unique_id}: {e}")
                        
                        # Download custom emoji files
                        emoji_map = {}  # document_id -> filename
                        emoji_pattern = r'data-document-id="(\d+)"'
                        
                        # Collect all emoji document IDs from messages and reactions
                        emoji_doc_ids = set()
                        for msg in messages:
                            # Extract emoji document IDs from message text (custom-emoji-container tags)
                            for match in re.finditer(emoji_pattern, msg.get("text", "")):
                                doc_id = int(match.group(1))
                                emoji_doc_ids.add(doc_id)
                            
                            # Also extract emoji document IDs from reactions (custom-emoji-reaction img tags)
                            reactions_str = msg.get("reactions", "")
                            if reactions_str:
                                for match in re.finditer(emoji_pattern, reactions_str):
                                    doc_id = int(match.group(1))
                                    emoji_doc_ids.add(doc_id)
                        
                        # Download each unique emoji
                        for doc_id in emoji_doc_ids:
                            if doc_id in emoji_map:
                                continue
                            
                            # Try to get emoji from cache
                            try:
                                from telethon.tl.functions.messages import GetCustomEmojiDocumentsRequest  # pyright: ignore[reportMissingImports]
                                result = await client(GetCustomEmojiDocumentsRequest(document_id=[doc_id]))
                                
                                documents = None
                                if hasattr(result, "documents"):
                                    documents = result.documents
                                elif hasattr(result, "document"):
                                    documents = [result.document] if result.document else []
                                elif isinstance(result, list):
                                    documents = result
                                
                                if documents and len(documents) > 0:
                                    doc = documents[0]
                                    unique_id_emoji = get_unique_id(doc)
                                    if unique_id_emoji:
                                        # Check cache for emoji
                                        cached_emoji = None
                                        escaped_uid = glob.escape(unique_id_emoji)
                                        for config_dir in CONFIG_DIRECTORIES:
                                            config_media_dir = Path(config_dir) / "media"
                                            if config_media_dir.exists():
                                                for file_path in config_media_dir.glob(f"{escaped_uid}.*"):
                                                    if file_path.suffix.lower() != ".json":
                                                        cached_emoji = file_path
                                                        break
                                                if cached_emoji:
                                                    break
                                        
                                        if not cached_emoji:
                                            state_media_dir = get_state_media_path()
                                            if state_media_dir is not None and state_media_dir.exists():
                                                for file_path in state_media_dir.glob(f"{escaped_uid}.*"):
                                                    if file_path.suffix.lower() != ".json":
                                                        cached_emoji = file_path
                                                        break
                                        
                                        if cached_emoji and cached_emoji.exists():
                                            with open(cached_emoji, "rb") as f:
                                                emoji_bytes = f.read()
                                            mime_type = detect_mime_type_from_bytes(emoji_bytes[:1024])
                                            ext = get_file_extension_from_mime_or_bytes(mime_type, emoji_bytes)
                                            filename = f"emoji_{doc_id}{ext}"
                                            emoji_path = media_dir / filename
                                            with open(emoji_path, "wb") as f:
                                                f.write(emoji_bytes)
                                            emoji_map[doc_id] = filename
                                        else:
                                            # Download emoji
                                            emoji_bytes = await download_media_bytes(client, doc)
                                            mime_type = detect_mime_type_from_bytes(emoji_bytes[:1024])
                                            ext = get_file_extension_from_mime_or_bytes(mime_type, emoji_bytes)
                                            filename = f"emoji_{doc_id}{ext}"
                                            emoji_path = media_dir / filename
                                            with open(emoji_path, "wb") as f:
                                                f.write(emoji_bytes)
                                            emoji_map[doc_id] = filename
                                            # Cache to persistent storage (use unique_id for lookup)
                                            emoji_cache_filename = f"{unique_id_emoji}{ext}"
                                            _cache_media_to_state(unique_id_emoji, emoji_cache_filename, emoji_bytes)
                            except Exception as e:
                                logger.warning(f"Error downloading emoji {doc_id}: {e}")
                        
                        # Build lottie_data_map for TGS files (embedded JSON works with file:// and http)
                        lottie_data_map = _build_lottie_data_map(media_dir, media_map)

                        # Generate HTML
                        agent_tz_id = agent.get_timezone_identifier()
                        html_content = _generate_standalone_html(
                            agent_config_name, user_id, messages, translations,
                            agent_tz_id, media_map, mime_map, emoji_map, lottie_data_map,
                            include_translations,
                        )
                        
                        # Write HTML
                        html_path = temp_path / "index.html"
                        with open(html_path, "w", encoding="utf-8") as f:
                            f.write(html_content)
                        
                        # Create zip file (omit .tgs from media/ - Lottie JSON is embedded in HTML)
                        zip_buffer = io.BytesIO()
                        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                            zip_file.write(html_path, "index.html")
                            for media_file in media_dir.iterdir():
                                if media_file.suffix.lower() != ".tgs":
                                    zip_file.write(media_file, f"media/{media_file.name}")
                        
                        zip_buffer.seek(0)
                        return zip_buffer.getvalue()
                
                except Exception as e:
                    logger.error(f"Error downloading conversation: {e}", exc_info=True)
                    raise
            
            # Execute async function
            try:
                zip_data = agent.execute(_download_conversation(), timeout=300.0)  # 5 minute timeout
                
                # Return zip file as download
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"conversation_{agent_config_name}_{user_id}_{timestamp}.zip"
                
                # Properly format Content-Disposition header per RFC 6266
                # Escape quotes and backslashes in filename, then wrap in quotes
                escaped_filename = filename.replace('\\', '\\\\').replace('"', '\\"')
                # Use both filename (for compatibility) and filename* (RFC 5987 encoding for international chars)
                encoded_filename = quote(filename, safe='')
                content_disposition = f'attachment; filename="{escaped_filename}"; filename*=UTF-8\'\'{encoded_filename}'
                
                return Response(
                    zip_data,
                    mimetype="application/zip",
                    headers={
                        "Content-Disposition": content_disposition,
                        "Content-Length": str(len(zip_data))
                    }
                )
            except RuntimeError as e:
                error_msg = str(e).lower()
                if "not authenticated" in error_msg or "not running" in error_msg:
                    logger.warning(f"Agent {agent_config_name} client loop issue: {e}")
                    return jsonify({"error": "Agent client loop is not available"}), 503
                else:
                    logger.error(f"Error downloading conversation: {e}")
                    return jsonify({"error": str(e)}), 500
            except TimeoutError:
                logger.warning(f"Timeout downloading conversation for agent {agent_config_name}, user {user_id}")
                return jsonify({"error": "Timeout downloading conversation"}), 504
        except Exception as e:
            logger.error(f"Error downloading conversation for {agent_config_name}/{user_id}: {e}", exc_info=True)
            return jsonify({"error": str(e)}), 500


def _build_lottie_data_map(media_dir: Path, media_map: dict) -> dict:
    """
    Build a map of unique_id -> decompressed Lottie JSON for TGS files.

    Embedding the JSON inline allows the export to work when opened via file://
    (browsers block fetch() from file:// due to CORS) or served over http.
    """
    lottie_data_map = {}
    for unique_id, filename in media_map.items():
        if not filename.lower().endswith(".tgs"):
            continue
        tgs_path = media_dir / filename
        if not tgs_path.exists():
            continue
        try:
            with gzip.open(tgs_path, "rb") as f:
                decompressed = f.read().decode("utf-8")
            lottie_data_map[unique_id] = json_lib.loads(decompressed)
        except (gzip.BadGzipFile, json_lib.JSONDecodeError, OSError) as e:
            logger.debug(f"Could not decompress TGS for {unique_id}: {e}")
    return lottie_data_map


def _generate_standalone_html(
    agent_name: str, user_id: str, messages: list,
    translations: dict, agent_timezone: str, media_map: dict, mime_map: dict, emoji_map: dict,
    lottie_data_map: dict, show_translations: bool,
) -> str:
    """Generate standalone HTML file for conversation display."""
    # This will be a large HTML string with embedded CSS and JavaScript
    # Similar to the renderConversation function in admin_console.html
    
    # Get Lottie and pako from CDN (same as admin console)
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Conversation: {html.escape(agent_name)} / {html.escape(user_id)}</title>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/lottie-web/5.12.2/lottie.min.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 16px;
            background-color: #f5f5f5;
            max-width: 1200px;
            margin: 0 auto;
        }}
        .message {{
            background: white;
            padding: 12px;
            margin-bottom: 8px;
            border-radius: 8px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.1);
            border-left: 4px solid #4caf50;
        }}
        .message.agent {{
            background: #e3f2fd;
            border-left-color: #2196f3;
        }}
        .message-header {{
            font-size: 12px;
            color: #666;
            margin-bottom: 4px;
        }}
        .message-content {{
            white-space: pre-wrap;
            margin-bottom: 4px;
        }}
        .message-media {{
            margin: 8px 0;
        }}
        .message-media img {{
            max-width: 300px;
            max-height: 300px;
            border-radius: 8px;
        }}
        .message-media video {{
            max-width: 300px;
            max-height: 300px;
            border-radius: 8px;
        }}
        .message-media audio {{
            width: 100%;
            max-width: 400px;
        }}
        .tgs-container {{
            width: 200px;
            height: 200px;
            position: relative;
            display: flex;
            align-items: center;
            justify-content: center;
            background: #f8f9fa;
            border-radius: 4px;
            border: 1px solid #e9ecef;
            overflow: hidden;
        }}
        .tgs-container svg {{
            max-width: 100%;
            max-height: 100%;
        }}
        .reactions {{
            font-size: 11px;
            color: #888;
            margin-top: 4px;
            font-style: italic;
        }}
        .custom-emoji-container {{
            display: inline-block;
        }}
        .custom-emoji-img {{
            width: 1.2em;
            height: 1.2em;
            vertical-align: middle;
        }}
    </style>
</head>
<body>
    <h1>Conversation: {html.escape(agent_name)} / {html.escape(user_id)}</h1>
"""
    
    # Add messages
    html_content += '<h2>Messages</h2>\n'
    for msg in messages:
        msg_id = str(msg.get("id", ""))
        is_from_agent = msg.get("is_from_agent", False)
        sender_name = msg.get("sender_name", "User")
        sender_id = msg.get("sender_id")
        timestamp = msg.get("timestamp", "")
        reply_to = msg.get("reply_to_msg_id")
        reactions = msg.get("reactions", "")
        
        # Format timestamp in agent's timezone
        if timestamp:
            try:
                # Parse ISO timestamp (assumed to be in UTC)
                dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                # Ensure it's UTC-aware if timezone-naive
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                # Convert to agent's timezone
                if agent_timezone:
                    try:
                        agent_tz = ZoneInfo(agent_timezone)
                        dt_local = dt.astimezone(agent_tz)
                        formatted_time = dt_local.strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        # If timezone conversion fails, fall back to UTC formatting
                        formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    # No agent timezone specified, use UTC
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                formatted_time = timestamp
        else:
            formatted_time = "N/A"
        
        # Get text (translated if requested and available)
        text = msg.get("text", "")
        if show_translations and msg_id in translations:
            text = translations[msg_id]
        
        # Replace emoji URLs with local paths
        text = _replace_emoji_urls_with_local(text, emoji_map)
        
        # Build content from parts
        content_html = ""
        parts = msg.get("parts", [])
        if parts:
            # Track if we've already applied the translation to a text part
            translation_applied = False
            for part in parts:
                if part.get("kind") == "text":
                    # Use translated text if available and not already applied
                    if show_translations and msg_id in translations and not translation_applied:
                        # Use the translated text (already processed for emoji URLs on line 741)
                        part_text = text
                        translation_applied = True
                    else:
                        # Skip this text part if translation was already applied (to avoid duplication)
                        if translation_applied:
                            continue
                        # Use original part text and process emoji URLs
                        part_text = part.get("text", "")
                        part_text = _replace_emoji_urls_with_local(part_text, emoji_map)
                    content_html += f'<div class="message-content">{part_text}</div>\n'
                elif part.get("kind") == "media":
                    unique_id = part.get("unique_id")
                    media_kind = part.get("media_kind", "media")
                    is_animated = part.get("is_animated", False) or media_kind == "animated_sticker"
                    
                    if unique_id in media_map:
                        media_path = f"media/{media_map[unique_id]}"
                        filename = media_map[unique_id]
                        ext = Path(filename).suffix.lower()
                        mime_type = mime_map.get(unique_id, "")
                        if not mime_type:
                            inferred_type, _ = mimetypes.guess_type(media_path)
                            mime_type = inferred_type or ""

                        # Use file format to choose element: .webm/.mp4 need <video>, .tgs needs Lottie, audio needs <audio>, else <img>
                        is_video_format = ext in (".webm", ".mp4") or (
                            mime_type and mime_type.startswith("video/")
                        )
                        is_tgs_format = ext == ".tgs" or (
                            mime_type and "tgsticker" in mime_type.lower()
                        )
                        is_audio_format = ext in (".ogg", ".opus", ".m4a", ".mp3", ".wav", ".flac") or (
                            mime_type and mime_type.startswith("audio/")
                        )

                        if is_tgs_format:
                            # TGS (Lottie) - will be loaded by JavaScript from embedded lottie_data_map
                            escaped_unique_id = html.escape(unique_id)
                            content_html += f'<div class="message-media"><div class="tgs-container" id="tgs-{escaped_unique_id}" data-unique-id="{escaped_unique_id}" data-path="{html.escape(media_path)}"></div></div>\n'
                        elif is_video_format:
                            type_attr = f' type="{html.escape(mime_type)}"' if mime_type else ""
                            content_html += f'<div class="message-media"><video controls autoplay loop muted><source src="{html.escape(media_path)}"{type_attr}></video></div>\n'
                        elif is_audio_format:
                            type_attr = f' type="{html.escape(mime_type)}"' if mime_type else ""
                            content_html += f'<div class="message-media"><audio controls><source src="{html.escape(media_path)}"{type_attr}></audio></div>\n'
                        elif ext in (".webp", ".png", ".jpg", ".jpeg", ".gif"):
                            # Static images
                            sticker_name = part.get("sticker_name") or unique_id
                            content_html += f'<div class="message-media"><img src="{html.escape(media_path)}" alt="{html.escape(sticker_name)}"></div>\n'
                        else:
                            # Unknown media type - rendered_text with download link
                            rendered_text = part.get("rendered_text", "")
                            content_html += f'<div class="message-media" style="color: #666; font-style: italic;">{html.escape(rendered_text)} <a href="{html.escape(media_path)}" download>[Download]</a></div>\n'
                        
                        # Add caption for known media types only (unknown types already include rendered_text in the else branch)
                        is_unknown_media_type = not (
                            is_tgs_format or is_video_format or is_audio_format or
                            ext in (".webp", ".png", ".jpg", ".jpeg", ".gif")
                        )
                        if part.get("rendered_text") and not is_unknown_media_type:
                            content_html += f'<div style="color: #666; font-size: 11px; margin-top: 2px; font-style: italic;">{html.escape(part.get("rendered_text", ""))}</div>\n'
        else:
            # Fallback to text
            if text:
                content_html = f'<div class="message-content">{text}</div>\n'
            else:
                content_html = '<div class="message-content">[No content]</div>\n'
        
        # Build metadata
        metadata = f"{html.escape(sender_name)}"
        if sender_id:
            metadata += f" ({html.escape(str(sender_id))})"
        metadata += f" • {formatted_time} • ID: {html.escape(msg_id)}"
        if reply_to:
            metadata += f" • Reply to: {html.escape(str(reply_to))}"
        
        # Build message HTML
        msg_class = "message agent" if is_from_agent else "message"
        html_content += f"""    <div class="{msg_class}" id="msg-{html.escape(msg_id)}">
        <div class="message-header">{metadata}</div>
        {content_html}
"""
        if reactions:
            # Replace emoji URLs in reactions with local paths
            reactions_local = _replace_emoji_urls_with_local(reactions, emoji_map)
            html_content += f'        <div class="reactions">Reactions: {reactions_local}</div>\n'
        html_content += "    </div>\n"
    
    # Embed Lottie JSON inline so TGS works with file:// (CORS blocks fetch) and http
    lottie_json = json_lib.dumps(lottie_data_map)
    # Escape </ to prevent closing script tag when embedding in HTML
    lottie_json_safe = lottie_json.replace("</", "<\\/")

    html_content += (
        f'<script id="lottie-data" type="application/json">{lottie_json_safe}</script>\n'
        """
    <script>
        // Load TGS animations - use embedded LOTTIE_DATA (works with file:// and http)
        document.addEventListener('DOMContentLoaded', function() {
            const lottieDataEl = document.getElementById('lottie-data');
            const LOTTIE_DATA = lottieDataEl ? JSON.parse(lottieDataEl.textContent) : {};

            const tgsContainers = document.querySelectorAll('.tgs-container');
            tgsContainers.forEach(function(container) {
                const uniqueId = container.getAttribute('data-unique-id');
                const jsonData = LOTTIE_DATA[uniqueId];

                if (jsonData) {
                    try {
                        container.innerHTML = '';
                        const animationContainer = document.createElement('div');
                        animationContainer.style.width = '100%';
                        animationContainer.style.height = '100%';
                        animationContainer.style.display = 'flex';
                        animationContainer.style.alignItems = 'center';
                        animationContainer.style.justifyContent = 'center';
                        animationContainer.style.backgroundColor = '#ffffff';
                        container.appendChild(animationContainer);

                        lottie.loadAnimation({
                            container: animationContainer,
                            renderer: 'svg',
                            loop: true,
                            autoplay: true,
                            animationData: jsonData
                        });
                    } catch (e) {
                        console.error('Failed to load TGS animation:', e);
                        container.innerHTML = '<div style="text-align: center; color: #dc3545;">⚠️ Animation Error</div>';
                    }
                } else {
                    container.innerHTML = '<div style="text-align: center; color: #dc3545;">⚠️ Animation Error</div>';
                }
            });
        });
    </script>
</body>
</html>
"""
    )
    
    return html_content


def _replace_emoji_urls_with_local(html_text: str, emoji_map: dict) -> str:
    """Replace emoji URLs in HTML with local file paths."""
    if not html_text:
        return html_text
    
    # First, handle img tags that have data-emoji-url attributes
    # These may also have src attributes, which we need to update instead of creating duplicates
    # Pattern: <img ... data-emoji-url="/admin/api/agents/.../emoji/12345" ...>
    pattern_img_with_data_emoji = r'(<img[^>]*?data-emoji-url="[^"]*emoji/(\d+)"[^>]*?>)'
    def replace_img_with_data_emoji(match):
        img_tag = match.group(1)
        doc_id = int(match.group(2))
        
        if doc_id not in emoji_map:
            return img_tag
        
        local_path = f'media/{emoji_map[doc_id]}'
        
        # Check if img tag already has a src attribute
        if 'src="' in img_tag or "src='" in img_tag:
            # Update existing src attribute and remove data-emoji-url
            # Pattern to match src="..." or src='...'
            img_tag = re.sub(r'src=["\'][^"\']*["\']', f'src="{local_path}"', img_tag)
            # Remove data-emoji-url attribute
            img_tag = re.sub(r'\s*data-emoji-url="[^"]*"', '', img_tag)
        else:
            # No src attribute, replace data-emoji-url with src
            img_tag = re.sub(r'data-emoji-url="[^"]*emoji/\d+"', f'src="{local_path}"', img_tag)
        
        return img_tag
    
    html_text = re.sub(pattern_img_with_data_emoji, replace_img_with_data_emoji, html_text)
    
    # Handle span elements with custom-emoji-container that have data-emoji-url
    # These contain inner img tags that need their src updated
    # Pattern: <span ... data-emoji-url="...emoji/12345" ...><img ... /></span>
    pattern_span_with_emoji = r'(<span[^>]*?data-emoji-url="[^"]*emoji/(\d+)"[^>]*?>)(.*?)(</span>)'
    def replace_span_emoji(match):
        span_open = match.group(1)
        doc_id = int(match.group(2))
        span_content = match.group(3)
        span_close = match.group(4)
        
        if doc_id not in emoji_map:
            return match.group(0)
        
        local_path = f'media/{emoji_map[doc_id]}'
        
        # Update the inner img tag's src attribute if it exists
        if '<img' in span_content:
            # Update img src within the span content to use local path
            # Match img tags with src containing emoji URLs
            span_content = re.sub(
                r'(<img[^>]*?src=")[^"]*emoji/(\d+)"([^>]*?>)',
                lambda m: f'{m.group(1)}{local_path}"{m.group(3)}' if int(m.group(2)) == doc_id else m.group(0),
                span_content
            )
        
        # Update data-emoji-url on the span to point to local path (for consistency)
        span_open = re.sub(
            r'data-emoji-url="[^"]*emoji/\d+"',
            f'data-emoji-url="{local_path}"',
            span_open
        )
        
        return f'{span_open}{span_content}{span_close}'
    
    html_text = re.sub(pattern_span_with_emoji, replace_span_emoji, html_text, flags=re.DOTALL)
    
    # Note: We don't replace remaining data-emoji-url attributes on other elements
    # because we can't safely convert them to src (which is only valid for img elements).
    # The img and span cases have been handled above.
    
    # Also replace img src URLs for emojis (in case src wasn't updated by previous patterns)
    # Pattern: <img ... src="/admin/api/agents/.../emoji/12345" ...>
    pattern2 = r'(<img[^>]*src=")[^"]*emoji/(\d+)"([^>]*>)'
    def replace_emoji_img(match):
        doc_id = int(match.group(2))
        if doc_id in emoji_map:
            return f'{match.group(1)}media/{emoji_map[doc_id]}"{match.group(3)}'
        return match.group(0)
    
    html_text = re.sub(pattern2, replace_emoji_img, html_text)
    
    return html_text
