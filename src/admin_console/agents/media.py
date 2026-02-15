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
from io import BytesIO
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request, send_file  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.messages import SendMediaRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.photos import (  # pyright: ignore[reportMissingImports]
    DeletePhotosRequest,
    UploadProfilePhotoRequest,
)
from telethon.tl.types import (  # pyright: ignore[reportMissingImports]
    InputMediaPhoto,
    InputPeerSelf,
    InputPhoto,
)

from admin_console.helpers import (
    get_agent_by_name,
    get_state_media_path,
    is_state_media_directory,
    resolve_media_path,
)
from config import CONFIG_DIRECTORIES
from db import media_metadata
from db import agent_profile_photos
from media.media_sources import get_directory_media_source
from telegram_download import download_media_bytes
from telegram_media import get_unique_id

logger = logging.getLogger(__name__)


async def _list_agent_media(agent, client) -> list[dict[str, Any]]:
    """
    List media from agent's Saved Messages with profile photo indicators.
    
    Args:
        agent: Agent instance
        client: Telethon client
        
    Returns:
        List of media items from Saved Messages with is_profile_photo flags
    """
    media_by_unique_id: dict[str, dict[str, Any]] = {}
    
    # Get agent's Telegram ID and current profile photos
    try:
        me = await client.get_me()
        agent_telegram_id = me.id
        
        # Get current profile photo unique_ids
        profile_photo_unique_ids = []
        profile_photos = await client.get_profile_photos(me, limit=None)
        for photo in profile_photos:
            unique_id_val = get_unique_id(photo)
            if unique_id_val:
                profile_photo_unique_ids.append(str(unique_id_val))
        
        # Query database for source media that have profile photos
        source_media_with_profiles = agent_profile_photos.get_source_media_with_profile_photos(
            agent_telegram_id, 
            profile_photo_unique_ids
        )
        
        # ORPHAN RECOVERY: Copy unmapped profile photos to Saved Messages
        # This handles profile photos that existed before this feature, or after DB reset
        for profile_photo_id in profile_photo_unique_ids:
            mapping = agent_profile_photos.get_sources_for_profile_photos(
                agent_telegram_id, [profile_photo_id]
            )
            if not mapping:  # Orphaned - has no source mapping
                logger.info(f"Found orphaned profile photo {profile_photo_id}, copying to Saved Messages")
                
                # Find the profile photo object
                for photo in profile_photos:
                    if str(get_unique_id(photo)) == profile_photo_id:
                        try:
                            # Copy to Saved Messages using SendMediaRequest (preserves unique_id!)
                            result = await client(SendMediaRequest(
                                peer=InputPeerSelf(),
                                media=InputMediaPhoto(
                                    id=InputPhoto(
                                        id=photo.id,
                                        access_hash=photo.access_hash,
                                        file_reference=photo.file_reference
                                    )
                                ),
                                message=""
                            ))
                            
                            # Create self-mapping: profile photo IS its own source
                            agent_profile_photos.add_profile_photo_mapping(
                                agent_telegram_id,
                                profile_photo_id,
                                profile_photo_id  # Same ID!
                            )
                            
                            source_media_with_profiles.add(profile_photo_id)
                            logger.info(f"Recovered orphaned profile photo {profile_photo_id}")
                        except Exception as e:
                            logger.error(f"Failed to recover orphaned profile photo {profile_photo_id}: {e}")
                        break
                
    except Exception as e:
        logger.error(f"Error loading profile photo mappings for {agent.name}: {e}")
        source_media_with_profiles = set()
    
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
                    
                    # Check if this source media has a profile photo
                    is_profile = unique_id_str in source_media_with_profiles
                    
                    # Create media entry (only from Saved Messages now)
                    if unique_id_str not in media_by_unique_id:
                        media_by_unique_id[unique_id_str] = {
                            "unique_id": unique_id_str,
                            "is_profile_photo": is_profile,
                            "can_be_profile_photo": True,
                            "media_kind": media_kind,
                            "description": None,
                            "status": None,
                            "message_id": message.id,
                        }
            
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
                    
                    # Check if this source media has a profile photo
                    is_profile = unique_id_str in source_media_with_profiles
                    
                    # Create media entry (only from Saved Messages now)
                    if unique_id_str not in media_by_unique_id:
                        media_by_unique_id[unique_id_str] = {
                            "unique_id": unique_id_str,
                            "is_profile_photo": is_profile,
                            "can_be_profile_photo": can_be_profile,
                            "media_kind": media_kind,
                            "description": None,
                            "status": None,
                            "message_id": message.id,
                        }
                    
    except Exception as e:
        logger.error(f"Error loading Saved Messages photos for {agent.name}: {e}")
    
    # Load descriptions and status from agent's config directory only
    # (Agent media should never use MySQL cache - that's only for state/media)
    for unique_id_str, media_item in media_by_unique_id.items():
        try:
            # Load from config directory only
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
                                if config_record.get("status"):
                                    media_item["status"] = config_record["status"]
                        except Exception as e:
                            logger.debug(f"Error reading config media JSON for {unique_id_str}: {e}")
        except Exception as e:
            logger.debug(f"Error loading metadata for {unique_id_str}: {e}")
    
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
    # Get agent's Telegram ID
    me = await client.get_me()
    agent_telegram_id = me.id
    
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
    
    # Determine file extension
    is_video = hasattr(media_obj, "mime_type") and getattr(media_obj, "mime_type", "").startswith("video/")
    file_ext = ".mp4" if is_video else ".jpg"
    
    # Upload as profile photo with proper filename
    uploaded_file = await client.upload_file(media_bytes, file_name=f"profile{file_ext}")
    
    if is_video:
        # Upload video profile photo
        result = await client(UploadProfilePhotoRequest(video=uploaded_file))
    else:
        # Upload photo profile photo
        result = await client(UploadProfilePhotoRequest(file=uploaded_file))
    
    # Extract new profile photo unique_id and record mapping
    if hasattr(result, 'photo'):
        new_profile_photo = result.photo
        new_profile_unique_id = get_unique_id(new_profile_photo)
        if new_profile_unique_id:
            agent_profile_photos.add_profile_photo_mapping(
                agent_telegram_id,
                str(new_profile_unique_id),
                unique_id  # source media unique_id
            )
            logger.info(f"Recorded profile photo mapping: {new_profile_unique_id} -> {unique_id}")
    
    return True


async def _remove_profile_photo(agent, client, source_unique_id: str) -> bool:
    """
    Remove profile photos linked to this source media.
    
    Args:
        agent: Agent instance
        client: Telethon client
        source_unique_id: Source media unique ID
        
    Returns:
        True if successful
    """
    # Get agent's Telegram ID
    me = await client.get_me()
    agent_telegram_id = me.id
    
    # Get profile photo IDs for this source
    profile_photo_ids = agent_profile_photos.get_profile_photos_for_source(
        agent_telegram_id,
        source_unique_id
    )
    
    if not profile_photo_ids:
        logger.info(f"No profile photos found for source {source_unique_id}")
        return True
    
    # Get current profile photos
    profile_photos = await client.get_profile_photos(me, limit=None)
    
    # Delete matching profile photos
    deleted_count = 0
    for photo in profile_photos:
        photo_unique_id = get_unique_id(photo)
        if photo_unique_id and str(photo_unique_id) in profile_photo_ids:
            try:
                await client(DeletePhotosRequest(
                    id=[InputPhoto(
                        id=photo.id,
                        access_hash=photo.access_hash,
                        file_reference=photo.file_reference
                    )]
                ))
                
                agent_profile_photos.remove_profile_photo_mapping(
                    agent_telegram_id,
                    str(photo_unique_id)
                )
                deleted_count += 1
                logger.info(f"Deleted profile photo {photo_unique_id} for source {source_unique_id}")
            except Exception as e:
                logger.error(f"Error deleting profile photo {photo_unique_id}: {e}")
    
    logger.info(f"Deleted {deleted_count} profile photo(s) for source {source_unique_id}")
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
    Get media bytes for display (full resolution, CSS handles sizing).
    
    Args:
        agent: Agent instance
        client: Telethon client
        unique_id: Media unique ID
        
    Returns:
        Media bytes or None
    """
    # Try to find media in Saved Messages or Profile Photos
    media_obj = None
    
    # Check Saved Messages
    async for message in client.iter_messages("me", limit=None):
        photo = getattr(message, "photo", None)
        if photo:
            unique_id_val = get_unique_id(photo)
            if unique_id_val and str(unique_id_val) == unique_id:
                media_obj = photo
                break
        
        # Check documents (for videos)
        document = getattr(message, "document", None)
        if document:
            unique_id_val = get_unique_id(document)
            if unique_id_val and str(unique_id_val) == unique_id:
                media_obj = document
                break
    
    # If not found, check profile photos
    if not media_obj:
        me = await client.get_me()
        profile_photos = await client.get_profile_photos(me, limit=None)
        for photo in profile_photos:
            unique_id_val = get_unique_id(photo)
            if unique_id_val and str(unique_id_val) == unique_id:
                media_obj = photo
                break
    
    if not media_obj:
        return None
    
    # Download full media (CSS handles sizing in the UI)
    return await download_media_bytes(client, media_obj)


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
            
            # Save to agent's config directory, not MySQL
            if not hasattr(agent, "config_directory") or not agent.config_directory:
                return jsonify({"error": "Agent has no config directory"}), 400
            
            config_media_dir = Path(agent.config_directory) / "media"
            config_media_dir.mkdir(parents=True, exist_ok=True)
            
            json_file = config_media_dir / f"{unique_id}.json"
            
            # Load existing record or create new one
            record = {}
            if json_file.exists():
                import json
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        record = json.load(f)
                except Exception as e:
                    logger.warning(f"Error reading existing record for {unique_id}: {e}")
            
            # Update description and status
            record["unique_id"] = unique_id
            record["description"] = description
            record["status"] = "curated"
            
            # Save to config directory
            import json
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(record, f, indent=2, ensure_ascii=False)
            
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
            
            # Delete from agent's config directory
            if hasattr(agent, "config_directory") and agent.config_directory:
                config_media_dir = Path(agent.config_directory) / "media"
                json_file = config_media_dir / f"{unique_id}.json"
                if json_file.exists():
                    json_file.unlink()
                    logger.info(f"Deleted description cache for {unique_id} from {json_file}")
            
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
    
    @agents_bp.route("/api/agents/<agent_config_name>/media/<unique_id>/file", methods=["GET"])
    def api_get_media_file(agent_config_name: str, unique_id: str):
        """Get media file."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400
            
            media_bytes = agent.execute(
                _get_media_thumbnail(agent, agent.client, unique_id),
                timeout=30.0
            )
            
            if not media_bytes:
                return jsonify({"error": "Media not found"}), 404
            
            # Return raw image bytes
            mime_type = "image/jpeg"  # Default for Telegram photos
            return send_file(
                BytesIO(media_bytes),
                mimetype=mime_type,
                as_attachment=False
            )
            
        except Exception as e:
            logger.error(f"Error getting media file for {unique_id}: {e}")
            return jsonify({"error": str(e)}), 500
