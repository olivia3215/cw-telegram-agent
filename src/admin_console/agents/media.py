# src/admin_console/agents/media.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Agent-specific media management routes for the admin console.
Manages media in agent's Saved Messages and profile photos.
"""

import base64
import logging
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.photos import (  # pyright: ignore[reportMissingImports]
    DeletePhotosRequest,
    UploadProfilePhotoRequest,
)
from telethon.tl.types import InputPhoto  # pyright: ignore[reportMissingImports]

from admin_console.helpers import (
    get_agent_by_name,
    get_state_media_path,
    is_state_media_directory,
    resolve_media_path,
)
from config import CONFIG_DIRECTORIES
from db import media_metadata
from media.media_sources import get_directory_media_source
from telegram_download import download_media_bytes
from telegram_media import get_unique_id

logger = logging.getLogger(__name__)


async def _list_agent_media(agent, client) -> list[dict[str, Any]]:
    """
    List media from agent's Saved Messages and Profile Photos.
    
    Args:
        agent: Agent instance
        client: Telethon client
        
    Returns:
        List of media items with deduplicated unique_ids
    """
    media_by_unique_id: dict[str, dict[str, Any]] = {}
    
    # Get profile photos
    try:
        me = await client.get_me()
        profile_photos = await client.get_profile_photos(me, limit=None)
        
        for photo in profile_photos:
            unique_id_val = get_unique_id(photo)
            if not unique_id_val:
                continue
                
            unique_id_str = str(unique_id_val)
            
            # Determine media kind (profile photos can be photo or video)
            # Check if it's a video by looking at photo attributes
            is_video = hasattr(photo, 'video_sizes') and photo.video_sizes
            media_kind = "video" if is_video else "photo"
            can_be_profile = True  # Photos and videos can be profile pictures
            
            # Get or create media entry
            if unique_id_str not in media_by_unique_id:
                media_by_unique_id[unique_id_str] = {
                    "unique_id": unique_id_str,
                    "is_profile_photo": True,
                    "can_be_profile_photo": can_be_profile,
                    "media_kind": media_kind,
                    "description": None,
                    "message_id": None,
                }
            else:
                # Mark existing entry as profile photo
                media_by_unique_id[unique_id_str]["is_profile_photo"] = True
                
    except Exception as e:
        logger.error(f"Error loading profile photos for {agent.name}: {e}")
    
    # Get photos from Saved Messages
    try:
        async for message in client.iter_messages("me", limit=None):
            # Check for photo
            photo = getattr(message, "photo", None)
            if photo:
                unique_id_val = get_unique_id(photo)
                if unique_id_val:
                    unique_id_str = str(unique_id_val)
                    
                    # Determine if it's a video profile photo
                    is_video = hasattr(photo, 'video_sizes') and photo.video_sizes
                    media_kind = "video" if is_video else "photo"
                    
                    # Get or create media entry
                    if unique_id_str not in media_by_unique_id:
                        media_by_unique_id[unique_id_str] = {
                            "unique_id": unique_id_str,
                            "is_profile_photo": False,
                            "can_be_profile_photo": True,
                            "media_kind": media_kind,
                            "description": None,
                            "message_id": message.id,
                        }
                    else:
                        # Update message_id if not set
                        if media_by_unique_id[unique_id_str]["message_id"] is None:
                            media_by_unique_id[unique_id_str]["message_id"] = message.id
            
            # Check for video/document (videos can also be profile pictures)
            document = getattr(message, "document", None)
            if document:
                mime_type = getattr(document, "mime_type", "")
                unique_id_val = get_unique_id(document)
                if unique_id_val:
                    unique_id_str = str(unique_id_val)
                    
                    # Determine media kind and profile photo eligibility
                    if mime_type.startswith("video/"):
                        media_kind = "video"
                        can_be_profile = True
                    elif mime_type.startswith("audio/"):
                        media_kind = "audio"
                        can_be_profile = False
                    elif mime_type == "application/x-tgsticker":
                        media_kind = "sticker"
                        can_be_profile = True  # Stickers can be profile pictures
                    elif mime_type.startswith("image/"):
                        # Image documents (like stickers)
                        media_kind = "sticker"
                        can_be_profile = True
                    else:
                        media_kind = "document"
                        can_be_profile = False
                    
                    # Get or create media entry
                    if unique_id_str not in media_by_unique_id:
                        media_by_unique_id[unique_id_str] = {
                            "unique_id": unique_id_str,
                            "is_profile_photo": False,
                            "can_be_profile_photo": can_be_profile,
                            "media_kind": media_kind,
                            "description": None,
                            "message_id": message.id,
                        }
                    else:
                        # Update message_id if not set
                        if media_by_unique_id[unique_id_str]["message_id"] is None:
                            media_by_unique_id[unique_id_str]["message_id"] = message.id
                    
    except Exception as e:
        logger.error(f"Error loading Saved Messages photos for {agent.name}: {e}")
    
    # Load descriptions from cache (state/media or config/media)
    for unique_id_str, media_item in media_by_unique_id.items():
        try:
            # Try MySQL cache first
            record = media_metadata.load_media_metadata(unique_id_str)
            if record and record.get("description"):
                media_item["description"] = record["description"]
            else:
                # Try config directory media
                if hasattr(agent, "config_directory") and agent.config_directory:
                    config_media_dir = Path(agent.config_directory) / "media"
                    if config_media_dir.exists():
                        json_file = config_media_dir / f"{unique_id_str}.json"
                        if json_file.exists():
                            import json
                            try:
                                with open(json_file, "r", encoding="utf-8") as f:
                                    config_record = json.load(f)
                                    if config_record.get("description"):
                                        media_item["description"] = config_record["description"]
                            except Exception as e:
                                logger.debug(f"Error reading config media JSON for {unique_id_str}: {e}")
        except Exception as e:
            logger.debug(f"Error loading description for {unique_id_str}: {e}")
    
    return list(media_by_unique_id.values())


async def _upload_media_to_saved_messages(agent, client, file_bytes: bytes, filename: str) -> dict[str, Any]:
    """
    Upload media to agent's Saved Messages.
    
    Args:
        agent: Agent instance
        client: Telethon client
        file_bytes: Media file bytes
        filename: Original filename
        
    Returns:
        Media metadata dict
    """
    # Upload to Saved Messages
    message = await client.send_file("me", file_bytes, attributes=[])
    
    # Get unique_id from the uploaded photo
    photo = getattr(message, "photo", None)
    if not photo:
        raise ValueError("Uploaded file did not result in a photo message")
    
    unique_id_val = get_unique_id(photo)
    if not unique_id_val:
        raise ValueError("Could not get unique_id from uploaded photo")
    
    unique_id_str = str(unique_id_val)
    
    return {
        "unique_id": unique_id_str,
        "message_id": message.id,
        "is_profile_photo": False,
        "can_be_profile_photo": True,
        "media_kind": "photo",
        "description": None,
    }


async def _set_as_profile_photo(agent, client, unique_id: str) -> bool:
    """
    Set media as profile photo (supports both photos and videos).
    
    Args:
        agent: Agent instance
        client: Telethon client
        unique_id: Media unique ID
        
    Returns:
        True if successful
    """
    # Find the media in Saved Messages (can be photo or video)
    media_obj = None
    async for message in client.iter_messages("me", limit=None):
        # Check photo
        photo = getattr(message, "photo", None)
        if photo:
            unique_id_val = get_unique_id(photo)
            if unique_id_val and str(unique_id_val) == unique_id:
                media_obj = photo
                break
        
        # Check video/document
        document = getattr(message, "document", None)
        if document:
            mime_type = getattr(document, "mime_type", "")
            if mime_type.startswith("video/"):
                unique_id_val = get_unique_id(document)
                if unique_id_val and str(unique_id_val) == unique_id:
                    media_obj = document
                    break
    
    if not media_obj:
        raise ValueError(f"Media with unique_id {unique_id} not found in Saved Messages")
    
    # Download media bytes
    media_bytes = await download_media_bytes(client, media_obj)
    
    # Upload as profile photo (works for both photos and videos)
    uploaded_file = await client.upload_file(media_bytes)
    
    # Check if it's a video
    is_video = hasattr(media_obj, "mime_type") and getattr(media_obj, "mime_type", "").startswith("video/")
    
    if is_video:
        # Upload video profile photo
        await client(UploadProfilePhotoRequest(video=uploaded_file))
    else:
        # Upload photo profile photo
        await client(UploadProfilePhotoRequest(file=uploaded_file))
    
    return True


async def _remove_profile_photo(agent, client, unique_id: str) -> bool:
    """
    Remove photo from profile photos and ensure it's in Saved Messages.
    
    Args:
        agent: Agent instance
        client: Telethon client
        unique_id: Media unique ID
        
    Returns:
        True if successful
    """
    me = await client.get_me()
    profile_photos = await client.get_profile_photos(me, limit=None)
    
    # Find the photo with matching unique_id
    target_photo = None
    for photo in profile_photos:
        unique_id_val = get_unique_id(photo)
        if unique_id_val and str(unique_id_val) == unique_id:
            target_photo = photo
            break
    
    if not target_photo:
        raise ValueError(f"Photo with unique_id {unique_id} not found in profile photos")
    
    # Check if media already exists in Saved Messages
    already_in_saved = False
    async for message in client.iter_messages("me", limit=None):
        photo = getattr(message, "photo", None)
        if photo:
            msg_unique_id = get_unique_id(photo)
            if msg_unique_id and str(msg_unique_id) == unique_id:
                already_in_saved = True
                break
        
        # Check documents (for videos)
        document = getattr(message, "document", None)
        if document:
            msg_unique_id = get_unique_id(document)
            if msg_unique_id and str(msg_unique_id) == unique_id:
                already_in_saved = True
                break
    
    # If not in Saved Messages, upload it there first
    if not already_in_saved:
        # Download the media
        media_bytes = await download_media_bytes(client, target_photo)
        
        # Upload to Saved Messages
        await client.send_file("me", media_bytes, attributes=[])
        logger.info(f"Uploaded media {unique_id} to Saved Messages before removing from profile")
    
    # Now delete from profile photos
    await client(DeletePhotosRequest(
        id=[InputPhoto(
            id=target_photo.id,
            access_hash=target_photo.access_hash,
            file_reference=target_photo.file_reference
        )]
    ))
    
    return True


async def _delete_from_saved_messages(agent, client, unique_id: str) -> bool:
    """
    Delete media from Saved Messages.
    
    Args:
        agent: Agent instance
        client: Telethon client
        unique_id: Media unique ID
        
    Returns:
        True if successful
    """
    # Find the message with this photo
    message_to_delete = None
    async for message in client.iter_messages("me", limit=None):
        photo = getattr(message, "photo", None)
        if not photo:
            continue
            
        unique_id_val = get_unique_id(photo)
        if unique_id_val and str(unique_id_val) == unique_id:
            message_to_delete = message
            break
    
    if not message_to_delete:
        raise ValueError(f"Photo with unique_id {unique_id} not found in Saved Messages")
    
    # Delete the message
    await client.delete_messages("me", [message_to_delete.id])
    
    return True


async def _get_media_thumbnail(agent, client, unique_id: str) -> bytes | None:
    """
    Get thumbnail bytes for media.
    
    Args:
        agent: Agent instance
        client: Telethon client
        unique_id: Media unique ID
        
    Returns:
        Thumbnail bytes or None
    """
    import io
    
    # Try to find photo in Saved Messages or Profile Photos
    photo_obj = None
    
    # Check Saved Messages
    async for message in client.iter_messages("me", limit=None):
        photo = getattr(message, "photo", None)
        if photo:
            unique_id_val = get_unique_id(photo)
            if unique_id_val and str(unique_id_val) == unique_id:
                photo_obj = photo
                break
        
        # Check documents (for videos)
        document = getattr(message, "document", None)
        if document:
            unique_id_val = get_unique_id(document)
            if unique_id_val and str(unique_id_val) == unique_id:
                photo_obj = document
                break
    
    # If not found, check profile photos
    if not photo_obj:
        me = await client.get_me()
        profile_photos = await client.get_profile_photos(me, limit=None)
        for photo in profile_photos:
            unique_id_val = get_unique_id(photo)
            if unique_id_val and str(unique_id_val) == unique_id:
                photo_obj = photo
                break
    
    if not photo_obj:
        return None
    
    # Download as thumbnail using Telethon's download_media with thumb parameter
    try:
        buf = io.BytesIO()
        # Use thumb=-1 to get the smallest thumbnail
        await client.download_media(photo_obj, file=buf, thumb=-1)
        return buf.getvalue()
    except Exception as e:
        # If thumbnail download fails, try downloading the full media
        logger.debug(f"Thumbnail download failed for {unique_id}, falling back to full media: {e}")
        try:
            return await download_media_bytes(client, photo_obj)
        except Exception as e2:
            logger.error(f"Full media download also failed for {unique_id}: {e2}")
            return None


def register_media_routes(agents_bp: Blueprint):
    """Register agent media management routes."""
    
    @agents_bp.route("/api/agents/<agent_config_name>/media", methods=["GET"])
    def api_list_agent_media(agent_config_name: str):
        """List media from agent's Saved Messages and Profile Photos."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400
            
            media_list = agent.execute(_list_agent_media(agent, agent.client), timeout=30.0)
            return jsonify({"media": media_list})
            
        except Exception as e:
            logger.error(f"Error listing media for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @agents_bp.route("/api/agents/<agent_config_name>/media/upload", methods=["POST"])
    def api_upload_agent_media(agent_config_name: str):
        """Upload media to agent's Saved Messages."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400
            
            # Get file from request
            if "file" not in request.files:
                return jsonify({"error": "No file provided"}), 400
            
            file = request.files["file"]
            if not file.filename:
                return jsonify({"error": "No filename"}), 400
            
            file_bytes = file.read()
            
            media_data = agent.execute(
                _upload_media_to_saved_messages(agent, agent.client, file_bytes, file.filename),
                timeout=30.0
            )
            return jsonify(media_data)
            
        except Exception as e:
            logger.error(f"Error uploading media for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @agents_bp.route("/api/agents/<agent_config_name>/media/<unique_id>", methods=["DELETE"])
    def api_delete_agent_media(agent_config_name: str, unique_id: str):
        """Delete media from agent's Saved Messages."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400
            
            success = agent.execute(
                _delete_from_saved_messages(agent, agent.client, unique_id),
                timeout=30.0
            )
            return jsonify({"success": success})
            
        except Exception as e:
            logger.error(f"Error deleting media {unique_id} for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @agents_bp.route("/api/agents/<agent_config_name>/media/<unique_id>/description", methods=["PUT"])
    def api_update_media_description(agent_config_name: str, unique_id: str):
        """Update media description."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            data = request.get_json()
            if not data or "description" not in data:
                return jsonify({"error": "No description provided"}), 400
            
            description = data["description"]
            
            # Update in MySQL cache
            record = media_metadata.load_media_metadata(unique_id)
            if not record:
                # Create new record
                record = {
                    "unique_id": unique_id,
                    "description": description,
                    "status": "curated",
                }
            else:
                record["description"] = description
                record["status"] = "curated"
            
            media_metadata.save_media_metadata(unique_id, record)
            
            return jsonify({"success": True, "description": description})
            
        except Exception as e:
            logger.error(f"Error updating description for {unique_id}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @agents_bp.route("/api/agents/<agent_config_name>/media/<unique_id>/refresh-description", methods=["POST"])
    def api_refresh_media_description(agent_config_name: str, unique_id: str):
        """Refresh media description using AI."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400
            
            # Delete existing cached description
            media_metadata.delete_media_metadata(unique_id)
            
            # The next time the media is accessed, it will be regenerated by AI
            return jsonify({"success": True, "message": "Description cache cleared, will regenerate on next access"})
            
        except Exception as e:
            logger.error(f"Error refreshing description for {unique_id}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @agents_bp.route("/api/agents/<agent_config_name>/media/<unique_id>/set-profile-photo", methods=["POST"])
    def api_set_profile_photo(agent_config_name: str, unique_id: str):
        """Set media as profile photo."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400
            
            success = agent.execute(
                _set_as_profile_photo(agent, agent.client, unique_id),
                timeout=30.0
            )
            return jsonify({"success": success})
            
        except Exception as e:
            logger.error(f"Error setting profile photo {unique_id} for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @agents_bp.route("/api/agents/<agent_config_name>/media/<unique_id>/profile-photo", methods=["DELETE"])
    def api_remove_profile_photo(agent_config_name: str, unique_id: str):
        """Remove photo from profile photos."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400
            
            success = agent.execute(
                _remove_profile_photo(agent, agent.client, unique_id),
                timeout=30.0
            )
            return jsonify({"success": success})
            
        except Exception as e:
            logger.error(f"Error removing profile photo {unique_id} for {agent_config_name}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @agents_bp.route("/api/agents/<agent_config_name>/media/<unique_id>/thumbnail", methods=["GET"])
    def api_get_media_thumbnail(agent_config_name: str, unique_id: str):
        """Get media thumbnail."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400
            
            thumbnail_bytes = agent.execute(
                _get_media_thumbnail(agent, agent.client, unique_id),
                timeout=30.0
            )
            
            if not thumbnail_bytes:
                return jsonify({"error": "Thumbnail not found"}), 404
            
            # Return as base64 data URL
            mime_type = "image/jpeg"  # Default for Telegram photos
            base64_data = base64.b64encode(thumbnail_bytes).decode("utf-8")
            data_url = f"data:{mime_type};base64,{base64_data}"
            
            return jsonify({"thumbnail": data_url})
            
        except Exception as e:
            logger.error(f"Error getting thumbnail for {unique_id}: {e}")
            return jsonify({"error": str(e)}), 500
