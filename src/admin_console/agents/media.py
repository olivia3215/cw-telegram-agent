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
import shutil
from io import BytesIO
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request, send_file  # pyright: ignore[reportMissingImports]
from telethon import TelegramClient  # pyright: ignore[reportMissingImports]
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
from media.agent_media import get_agent_media_dir
from media.file_resolver import find_media_file
from media.mime_utils import (
    classify_media_kind_from_mime_and_hint,
    detect_mime_type_from_bytes,
    get_file_extension_from_mime_or_bytes,
    get_file_extension_for_mime_type,
    is_image_mime_type,
)
from media.media_service import get_media_service
from media.media_sources import get_directory_media_source
from telegram_download import download_media_bytes
from telegram_media import get_unique_id, iter_media_parts

logger = logging.getLogger(__name__)


def _promote_media_to_agent_config(
    unique_id: str,
    config_media_dir: Path,
    *,
    agent: Any = None,
) -> None:
    """
    Move existing media/metadata into the agent config media directory.

    Promotion order:
    1) state/media (MySQL + file)
    2) other config directories' media folders

    This intentionally avoids synthesizing new records from downloaded previews.
    """
    try:
        config_media_dir.mkdir(parents=True, exist_ok=True)
        target_source = get_directory_media_source(config_media_dir)

        # Already present in target; no promotion needed.
        if target_source.get_cached_record(unique_id) or find_media_file(
            config_media_dir, unique_id
        ):
            return

        # 1) Promote from state/media (MySQL + file)
        state_media_dir = get_state_media_path()
        if state_media_dir and state_media_dir.exists():
            state_svc = get_media_service(state_media_dir)
            state_record = state_svc.get_record(unique_id)
            if state_record:
                record_to_write = state_record.copy()
                media_file = state_svc.resolve_media_file(unique_id, state_record)
                if media_file and media_file.exists():
                    record_to_write["media_file"] = media_file.name

                # Write metadata first, then move file.
                target_source.put(unique_id, record_to_write, agent=agent)
                if media_file and media_file.exists():
                    target_media = config_media_dir / media_file.name
                    media_file.replace(target_media)

                # Remove state copy after successful promotion.
                state_svc.delete_media_files(unique_id, record=state_record)
                state_svc.delete_record(unique_id)
                logger.info(
                    "Promoted media %s from state/media to %s",
                    unique_id,
                    config_media_dir,
                )
                return

        # 2) Promote from another config directory if present there.
        target_resolved = config_media_dir.resolve()
        for base in CONFIG_DIRECTORIES:
            source_media_dir = (Path(base) / "media").resolve()
            if source_media_dir == target_resolved:
                continue
            if not source_media_dir.exists() or not source_media_dir.is_dir():
                continue

            source = get_directory_media_source(source_media_dir)
            source_record = source.get_cached_record(unique_id)
            source_file = find_media_file(source_media_dir, unique_id)
            if not source_record and not source_file:
                continue

            if not source_record and source_file:
                # Legacy/file-only entry: create minimal metadata so we can move it cleanly.
                source.put(
                    unique_id,
                    {
                        "unique_id": unique_id,
                        "kind": "photo",
                        "status": "unknown",
                        "description": None,
                        "media_file": source_file.name,
                    },
                    agent=agent,
                )

            source.move_record_to(unique_id, target_source)
            logger.info(
                "Promoted media %s from %s to %s",
                unique_id,
                source_media_dir,
                config_media_dir,
            )
            return
    except Exception as e:
        logger.debug(
            "Failed promoting media %s into %s: %s", unique_id, config_media_dir, e
        )


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
                    media_kind = classify_media_kind_from_mime_and_hint(
                        "video/mp4" if is_video else "image/jpeg",
                        "video" if is_video else "photo",
                    )
                    
                    # Check if this source media has a profile photo
                    is_profile = unique_id_str in source_media_with_profiles
                    
                    # Create media entry (only from Saved Messages now)
                    if unique_id_str not in media_by_unique_id:
                        media_by_unique_id[unique_id_str] = {
                            "unique_id": unique_id_str,
                            "is_profile_photo": is_profile,
                            "can_be_profile_photo": True,
                            "media_kind": media_kind,
                            "mime_type": "video/mp4" if is_video else "image/jpeg",
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
                    attrs = getattr(document, "attributes", []) or []
                    is_sticker_attr = any(
                        getattr(attr.__class__, "__name__", "") == "DocumentAttributeSticker"
                        for attr in attrs
                    )

                    media_kind = classify_media_kind_from_mime_and_hint(
                        mime_type,
                        None,
                        has_sticker_attribute=is_sticker_attr,
                    )
                    # Telegram profile uploads support only photos/videos.
                    can_be_profile = media_kind in {"photo", "video"}
                    
                    # Check if this source media has a profile photo
                    is_profile = unique_id_str in source_media_with_profiles
                    
                    # Create media entry (only from Saved Messages now)
                    if unique_id_str not in media_by_unique_id:
                        media_by_unique_id[unique_id_str] = {
                            "unique_id": unique_id_str,
                            "is_profile_photo": is_profile,
                            "can_be_profile_photo": can_be_profile,
                            "media_kind": media_kind,
                            "mime_type": mime_type or None,
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
            # Load from config directory via DirectoryMediaSource (keeps in-memory cache consistent)
            try:
                config_media_dir = get_agent_media_dir(agent)
            except Exception:
                config_media_dir = None

            if config_media_dir:
                config_media_dir.mkdir(parents=True, exist_ok=True)
                dir_source = get_directory_media_source(config_media_dir)
                _promote_media_to_agent_config(
                    unique_id_str, config_media_dir, agent=agent
                )
                config_record = dir_source.get_cached_record(unique_id_str)
                if config_record:
                    if config_record.get("description"):
                        media_item["description"] = config_record["description"]
                    if config_record.get("status"):
                        media_item["status"] = config_record["status"]
                    if config_record.get("mime_type") and not media_item.get("mime_type"):
                        media_item["mime_type"] = config_record["mime_type"]
                    if config_record.get("media_file"):
                        media_item["media_file"] = config_record["media_file"]
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
    # Keep filename/extension so Telegram classifies images as photo messages.
    original_name = (filename or "").strip() or "upload"
    suffix = Path(original_name).suffix
    detected_mime = detect_mime_type_from_bytes(file_bytes[:1024]) if file_bytes else None
    if not suffix and detected_mime:
        ext = get_file_extension_for_mime_type(detected_mime)
        if ext and ext != "bin":
            original_name = f"{original_name}.{ext}"

    upload_file = BytesIO(file_bytes)
    upload_file.name = original_name

    # Don't force attributes for images; Telegram can downgrade to document when attrs are forced.
    send_kwargs: dict[str, Any] = {"force_document": False, "file": upload_file}

    # Upload to Saved Messages
    message = await client.send_file("me", **send_kwargs)

    # Accept both photo and document results; Telegram may store images as documents.
    media_obj = getattr(message, "photo", None) or getattr(message, "document", None)
    if not media_obj:
        raise ValueError("Uploaded file did not result in a media message")

    unique_id_val = get_unique_id(media_obj)
    if not unique_id_val:
        raise ValueError("Could not get unique_id from uploaded media")

    media_kind = "photo"
    can_be_profile = True
    document = getattr(message, "document", None)
    if document:
        mime_type = getattr(document, "mime_type", "") or ""
        attrs = getattr(document, "attributes", []) or []
        is_sticker_attr = any(
            getattr(attr.__class__, "__name__", "") == "DocumentAttributeSticker"
            for attr in attrs
        )
        media_kind = classify_media_kind_from_mime_and_hint(
            mime_type,
            None,
            has_sticker_attribute=is_sticker_attr,
        )
        can_be_profile = media_kind in {"photo", "video"}

    unique_id_str = str(unique_id_val)

    return {
        "unique_id": unique_id_str,
        "message_id": message.id,
        "is_profile_photo": False,
        "can_be_profile_photo": can_be_profile,
        "media_kind": media_kind,
        "mime_type": getattr(document, "mime_type", None) if document else ("image/jpeg" if media_kind == "photo" else None),
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
    
    # Find the media in Saved Messages by unique_id.
    media_obj = None
    matched_document_mime = ""
    matched_document_is_sticker = False
    async for message in client.iter_messages("me", limit=None):
        # Check photo
        photo = getattr(message, "photo", None)
        if photo:
            unique_id_val = get_unique_id(photo)
            if unique_id_val and str(unique_id_val) == unique_id:
                media_obj = photo
                break
        
        # Check any document (video/image/sticker/etc.)
        document = getattr(message, "document", None)
        if document:
            unique_id_val = get_unique_id(document)
            if unique_id_val and str(unique_id_val) == unique_id:
                media_obj = document
                matched_document_mime = (getattr(document, "mime_type", "") or "").lower()
                attrs = getattr(document, "attributes", []) or []
                matched_document_is_sticker = any(
                    getattr(attr.__class__, "__name__", "") == "DocumentAttributeSticker"
                    for attr in attrs
                )
                break
    
    if not media_obj:
        raise ValueError(f"Media with unique_id {unique_id} not found in Saved Messages")

    # Determine whether Telegram can accept this media as a profile photo.
    if hasattr(media_obj, "mime_type"):
        if matched_document_is_sticker or matched_document_mime == "application/x-tgsticker":
            raise ValueError(
                "This sticker cannot be set as a profile photo. Use a photo or video item instead."
            )
        if matched_document_mime.startswith("audio/"):
            raise ValueError(
                "Audio items cannot be set as profile photos. Use a photo or video item instead."
            )
        if matched_document_mime.startswith("video/"):
            is_video = True
        elif matched_document_mime.startswith("image/"):
            is_video = False
        else:
            raise ValueError(
                f"Unsupported media type for profile photo: {matched_document_mime or 'unknown'}"
            )
    else:
        # Telethon photo object (possibly video profile photo)
        is_video = bool(getattr(media_obj, "video_sizes", None))
    
    # Download media bytes
    media_bytes = await download_media_bytes(client, media_obj)
    
    # Determine file extension
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


async def _remove_profile_photo(
    agent,
    client,
    source_unique_id: str,
    *,
    ensure_media_in_saved_messages: bool = True,
) -> bool:
    """
    Remove profile photos linked to this source media.
    
    Before deletion, ensures the media exists in Saved Messages to prevent data loss.
    
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
    
    # Check if source media exists in Saved Messages
    media_in_saved_messages = False
    async for message in client.iter_messages("me", limit=None):
        media_obj = getattr(message, "photo", None) or getattr(message, "document", None)
        if media_obj:
            msg_unique_id = get_unique_id(media_obj)
            if msg_unique_id and str(msg_unique_id) == source_unique_id:
                media_in_saved_messages = True
                break
    
    # If source media is NOT in Saved Messages, copy it before deleting profile photo.
    # This safety behavior is skipped for explicit delete flows.
    if ensure_media_in_saved_messages and not media_in_saved_messages:
        logger.info(f"Source media {source_unique_id} not in Saved Messages, copying before profile photo deletion")
        profile_photos = await client.get_profile_photos(me, limit=None)
        
        # Find the profile photo that matches the source
        for photo in profile_photos:
            photo_unique_id = get_unique_id(photo)
            if photo_unique_id and str(photo_unique_id) in profile_photo_ids:
                try:
                    # Copy to Saved Messages using SendMediaRequest (preserves unique_id!)
                    await client(SendMediaRequest(
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
                    logger.info(f"Copied profile photo {photo_unique_id} to Saved Messages")
                    break
                except Exception as e:
                    logger.error(f"Failed to copy profile photo {photo_unique_id} to Saved Messages: {e}")
                    return False
    
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
    # Find the message with this media (photo or document).
    message_to_delete = None
    async for message in client.iter_messages("me", limit=None):
        media_obj = getattr(message, "photo", None) or getattr(message, "document", None)
        if not media_obj:
            continue

        unique_id_val = get_unique_id(media_obj)
        if unique_id_val and str(unique_id_val) == unique_id:
            message_to_delete = message
            break

    # If it's not in Saved Messages, handle profile-only/stale states gracefully.
    if not message_to_delete:
        me = await client.get_me()
        agent_telegram_id = me.id
        mapped_profile_ids = agent_profile_photos.get_profile_photos_for_source(
            agent_telegram_id,
            unique_id,
        )
        if mapped_profile_ids:
            logger.info(
                "Media %s not found in Saved Messages; removing %d linked profile photo(s)",
                unique_id,
                len(mapped_profile_ids),
            )
            await _remove_profile_photo(
                agent,
                client,
                unique_id,
                ensure_media_in_saved_messages=False,
            )
            return True

        logger.info(
            "Media %s already absent from Saved Messages and has no linked profile photos",
            unique_id,
        )
        return True

    # Delete the Saved Messages item.
    await client.delete_messages("me", [message_to_delete.id])

    # Also remove linked profile photos if this media is currently used as profile source.
    # Do not restore media into Saved Messages during explicit delete.
    try:
        await _remove_profile_photo(
            agent,
            client,
            unique_id,
            ensure_media_in_saved_messages=False,
        )
    except Exception as e:
        logger.debug("Non-fatal: failed removing linked profile photos for %s: %s", unique_id, e)

    return True


async def _get_media_thumbnail(agent, client, unique_id: str) -> bytes | None:
    """
    Get media bytes for display (full resolution, CSS handles sizing).
    
    Uses media pipeline to retrieve cached files when available.
    
    Args:
        agent: Agent instance
        client: Telethon client
        unique_id: Media unique ID
        
    Returns:
        Media bytes or None
    """
    # First, try to get from agent's config media directory via DirectoryMediaSource
    try:
        config_media_dir = get_agent_media_dir(agent)
    except Exception:
        config_media_dir = None

    if config_media_dir:
        config_media_dir.mkdir(parents=True, exist_ok=True)
        _promote_media_to_agent_config(unique_id, config_media_dir, agent=agent)
        dir_source = get_directory_media_source(config_media_dir)
        record = dir_source.get_cached_record(unique_id)
        
        if record and record.get("media_file"):
            media_file = config_media_dir / record["media_file"]
            if media_file.exists():
                logger.debug(f"Using cached media file for {unique_id}: {media_file}")
                try:
                    return media_file.read_bytes()
                except Exception as e:
                    logger.warning(f"Failed to read cached file {media_file}: {e}")
                    # Fall through to download from Telegram
    
    # Not cached, need to download from Telegram
    # Try to find media in Saved Messages or Profile Photos
    media_obj = None
    matched_message = None
    
    # Check Saved Messages
    async for message in client.iter_messages("me", limit=None):
        photo = getattr(message, "photo", None)
        if photo:
            unique_id_val = get_unique_id(photo)
            if unique_id_val and str(unique_id_val) == unique_id:
                media_obj = photo
                matched_message = message
                break
        
        # Check documents (for videos)
        document = getattr(message, "document", None)
        if document:
            unique_id_val = get_unique_id(document)
            if unique_id_val and str(unique_id_val) == unique_id:
                media_obj = document
                matched_message = message
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
    media_bytes = await download_media_bytes(client, media_obj)

    # Fallback import path:
    # If this unique_id exists nowhere (state/media nor any config media dir),
    # persist a new curated record/file from Saved Messages so Agent Media stops
    # redownloading it on each open and Media Editor can manage it.
    if media_bytes and config_media_dir:
        try:
            dir_source = get_directory_media_source(config_media_dir)
            already_in_target = bool(
                dir_source.get_cached_record(unique_id)
                or find_media_file(config_media_dir, unique_id)
            )

            exists_in_state = False
            state_media_dir = get_state_media_path()
            if state_media_dir and state_media_dir.exists():
                try:
                    exists_in_state = bool(
                        get_media_service(state_media_dir).get_record(unique_id)
                    )
                except Exception:
                    exists_in_state = False

            exists_in_other_config = False
            target_resolved = config_media_dir.resolve()
            for base in CONFIG_DIRECTORIES:
                other_media_dir = (Path(base) / "media").resolve()
                if other_media_dir == target_resolved:
                    continue
                if not other_media_dir.exists() or not other_media_dir.is_dir():
                    continue
                other_source = get_directory_media_source(other_media_dir)
                if other_source.get_cached_record(unique_id) or find_media_file(
                    other_media_dir, unique_id
                ):
                    exists_in_other_config = True
                    break

            if not already_in_target and not exists_in_state and not exists_in_other_config:
                # Use shared Telegram media parsing helper to preserve canonical sticker metadata.
                parsed_item = None
                if matched_message is not None:
                    try:
                        for item in iter_media_parts(matched_message):
                            if item.unique_id == unique_id:
                                parsed_item = item
                                break
                    except Exception as parse_error:
                        logger.debug(
                            "Failed parsing media metadata from message for %s: %s",
                            unique_id,
                            parse_error,
                        )

                resolved_sticker_set_name = None
                resolved_sticker_set_title = None
                if parsed_item is not None:
                    try:
                        is_sticker_item = (
                            parsed_item.is_sticker()
                            if hasattr(parsed_item, "is_sticker")
                            else str(getattr(parsed_item, "kind", "")) == "sticker"
                        )
                        if is_sticker_item:
                            # Reuse the same metadata resolver used by the media pipeline.
                            from media.media_injector import _maybe_get_sticker_set_metadata

                            (
                                resolved_sticker_set_name,
                                resolved_sticker_set_title,
                            ) = await _maybe_get_sticker_set_metadata(agent, parsed_item)
                    except Exception as sticker_meta_error:
                        logger.debug(
                            "Failed resolving sticker set metadata for %s: %s",
                            unique_id,
                            sticker_meta_error,
                        )

                hinted_mime = (
                    (parsed_item.mime if parsed_item else None)
                    or (getattr(media_obj, "mime_type", None) or "")
                ).lower()
                detected_mime = detect_mime_type_from_bytes(media_bytes[:1024])
                final_mime = hinted_mime or detected_mime
                if detected_mime and detected_mime != "application/octet-stream":
                    final_mime = detected_mime
                if final_mime == "application/gzip" and hinted_mime == "application/x-tgsticker":
                    final_mime = "application/x-tgsticker"

                if parsed_item is not None:
                    kind = parsed_item.kind.value
                else:
                    if hasattr(media_obj, "mime_type"):
                        if hinted_mime.startswith("video/"):
                            kind = "video"
                        elif hinted_mime.startswith("audio/"):
                            kind = "audio"
                        elif hinted_mime == "application/x-tgsticker":
                            kind = "sticker"
                        elif hinted_mime.startswith("image/"):
                            kind = "photo"
                        else:
                            kind = "document"
                    else:
                        kind = "photo"

                record: dict[str, Any] = {
                    "unique_id": unique_id,
                    "status": "unknown",
                    "description": None,
                    "kind": kind,
                }
                if final_mime:
                    record["mime_type"] = final_mime
                if parsed_item is not None:
                    sticker_set_name = (
                        resolved_sticker_set_name
                        or getattr(parsed_item, "sticker_set_name", None)
                    )
                    sticker_set_title = (
                        resolved_sticker_set_title
                        or getattr(parsed_item, "sticker_set_title", None)
                    )
                    if sticker_set_name and sticker_set_name != "(unknown)":
                        record["sticker_set_name"] = sticker_set_name
                    if sticker_set_title and sticker_set_title != "(unknown)":
                        record["sticker_set_title"] = sticker_set_title
                    if parsed_item.sticker_name:
                        record["sticker_name"] = parsed_item.sticker_name

                file_extension = get_file_extension_from_mime_or_bytes(
                    final_mime, media_bytes
                )
                if not file_extension:
                    file_extension = (
                        ".jpg" if kind == "photo" else ".mp4" if kind == "video" else None
                    )

                if file_extension:
                    dir_source.put(
                        unique_id,
                        record,
                        media_bytes=media_bytes,
                        file_extension=file_extension,
                        agent=agent,
                    )
                else:
                    dir_source.put(unique_id, record, agent=agent)
                logger.info(
                    "Imported Saved Messages media %s into %s (no prior cache record found)",
                    unique_id,
                    config_media_dir,
                )
        except Exception as e:
            logger.debug(
                "Failed fallback import of Saved Messages media %s: %s", unique_id, e
            )

    return media_bytes


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
            
            # Save to agent's config directory using DirectoryMediaSource
            # This ensures both the file AND the in-memory cache are updated
            config_media_dir = get_agent_media_dir(agent)
            config_media_dir.mkdir(parents=True, exist_ok=True)
            dir_source = get_directory_media_source(config_media_dir)
            
            # Load existing record
            record = dir_source.get_cached_record(unique_id)
            if not record:
                # Create new record
                record = {
                    "unique_id": unique_id,
                }
            
            # Update description and status
            record["description"] = description
            record["status"] = "curated"
            
            # Save using DirectoryMediaSource which updates both file and cache
            dir_source.put(unique_id, record)
            
            return jsonify({"success": True, "description": description, "status": "curated"})
            
        except Exception as e:
            logger.error(f"Error updating description for {unique_id}: {e}")
            return jsonify({"error": str(e)}), 500
    
    @agents_bp.route("/api/agents/<agent_config_name>/media/<unique_id>/refresh-description", methods=["POST"])
    def api_refresh_media_description(agent_config_name: str, unique_id: str):
        """Refresh media description using AI - identical to Media Editor's refresh."""
        try:
            agent = get_agent_by_name(agent_config_name)
            if not agent:
                return jsonify({"error": f"Agent '{agent_config_name}' not found"}), 404
            
            if not agent.client:
                return jsonify({"error": "Agent is not authenticated"}), 400
            
            # Get agent's config media directory
            config_media_dir = get_agent_media_dir(agent)
            config_media_dir.mkdir(parents=True, exist_ok=True)
            media_cache_source = get_directory_media_source(config_media_dir)
            
            # Load the record (or bootstrap one if media exists but metadata is not curated yet)
            data = media_cache_source.get_cached_record(unique_id)
            if not data:
                data = {"unique_id": unique_id, "kind": "photo"}
                logger.debug(
                    "Refresh-from-AI: bootstrapping missing metadata record for %s in agent %s",
                    unique_id,
                    agent_config_name,
                )
            
            # Force the AI pipeline to regenerate a fresh description
            logger.debug(
                "Refresh-from-AI: clearing cached description for %s in agent %s",
                unique_id,
                agent_config_name,
            )
            data["description"] = None
            data.pop("failure_reason", None)
            from media.media_source import MediaStatus
            data["status"] = MediaStatus.TEMPORARY_FAILURE.value
            
            # Save the updated record
            media_cache_source.put(unique_id, data)
            
            # Find the media file
            media_file = None
            if data.get("media_file"):
                candidate = config_media_dir / data["media_file"]
                if candidate.exists() and candidate.is_file() and candidate.suffix.lower() != ".json":
                    media_file = candidate
            
            if not media_file:
                from media.file_resolver import find_media_file
                media_file = find_media_file(config_media_dir, unique_id)
                if media_file and not data.get("media_file"):
                    try:
                        data["media_file"] = media_file.name
                        media_cache_source.put(unique_id, data)
                    except Exception as e:
                        logger.debug(f"Could not patch media_file for {unique_id}: {e}")

            # If the file is not in the agent config media dir, attempt promotion from state/media.
            if not media_file:
                try:
                    state_media_dir = get_state_media_path()
                    if state_media_dir and state_media_dir.exists():
                        state_svc = get_media_service(state_media_dir)
                        state_record = state_svc.get_record(unique_id)
                        state_media_file = state_svc.resolve_media_file(unique_id, state_record)
                        if state_media_file and state_media_file.exists():
                            config_media_dir.mkdir(parents=True, exist_ok=True)
                            promoted_target = config_media_dir / state_media_file.name
                            if not promoted_target.exists():
                                shutil.move(str(state_media_file), str(promoted_target))
                            media_file = promoted_target

                            merged = (state_record or {}).copy()
                            # Keep existing state metadata when present; only force refresh-specific fields.
                            for key, value in data.items():
                                if key in {"description", "status", "failure_reason", "unique_id", "media_file"}:
                                    merged[key] = value
                                elif key not in merged:
                                    merged[key] = value
                            merged["unique_id"] = unique_id
                            merged["media_file"] = promoted_target.name
                            media_cache_source.put(unique_id, merged)
                            data = merged

                            # Remove state metadata and any leftover state-side files to avoid duplicates.
                            try:
                                state_svc.delete_media_files(unique_id, record=state_record)
                            except Exception as e:
                                logger.debug("State media file cleanup failed for %s: %s", unique_id, e)
                            try:
                                state_svc.delete_record(unique_id)
                            except Exception as e:
                                logger.debug("State media metadata cleanup failed for %s: %s", unique_id, e)

                            logger.info(
                                "Refresh-from-AI: promoted %s from state/media to %s",
                                unique_id,
                                config_media_dir,
                            )
                except Exception as e:
                    logger.debug(
                        "Refresh-from-AI: state/media promotion failed for %s: %s",
                        unique_id,
                        e,
                    )

            # Last-resort fallback: pull media directly from Telegram and cache in config media dir.
            if not media_file:
                async def _download_from_telegram(client: TelegramClient):
                    from media.mime_utils import (
                        classify_media_from_bytes_and_hints,
                        get_file_extension_from_mime_or_bytes,
                    )

                    media_obj = None
                    object_kind_hint = "photo"
                    hinted_mime = "image/jpeg"
                    has_audio_attribute = False
                    has_sticker_attribute = False

                    async for message in client.iter_messages("me", limit=None):
                        photo = getattr(message, "photo", None)
                        if photo:
                            uid = get_unique_id(photo)
                            if uid and str(uid) == unique_id:
                                media_obj = photo
                                # Profile/video photos should be treated as video.
                                is_video_photo = bool(getattr(photo, "video_sizes", None))
                                object_kind_hint = "video" if is_video_photo else "photo"
                                hinted_mime = "video/mp4" if is_video_photo else "image/jpeg"
                                break

                        document = getattr(message, "document", None)
                        if document:
                            uid = get_unique_id(document)
                            if uid and str(uid) == unique_id:
                                media_obj = document
                                hinted_mime = getattr(document, "mime_type", None) or hinted_mime
                                attrs = getattr(document, "attributes", []) or []
                                has_audio_attribute = any(
                                    getattr(attr.__class__, "__name__", "") == "DocumentAttributeAudio"
                                    for attr in attrs
                                )
                                is_sticker_attr = any(
                                    getattr(attr.__class__, "__name__", "") == "DocumentAttributeSticker"
                                    for attr in attrs
                                )
                                has_sticker_attribute = has_sticker_attribute or is_sticker_attr
                                if hinted_mime.startswith("video/"):
                                    object_kind_hint = "video"
                                elif hinted_mime.startswith("audio/"):
                                    object_kind_hint = "audio"
                                elif hinted_mime == "application/x-tgsticker" or is_sticker_attr:
                                    object_kind_hint = "animated_sticker"
                                elif hinted_mime.startswith("image/"):
                                    object_kind_hint = "photo"
                                else:
                                    object_kind_hint = "document"
                                break

                    if not media_obj:
                        me = await client.get_me()
                        profile_photos = await client.get_profile_photos(me, limit=None)
                        for photo in profile_photos:
                            uid = get_unique_id(photo)
                            if uid and str(uid) == unique_id:
                                media_obj = photo
                                is_video_photo = bool(getattr(photo, "video_sizes", None))
                                object_kind_hint = "video" if is_video_photo else "photo"
                                hinted_mime = "video/mp4" if is_video_photo else "image/jpeg"
                                break

                    if not media_obj:
                        return None

                    media_bytes = await download_media_bytes(client, media_obj)
                    if not media_bytes:
                        return None

                    # Telegram MIME/kind hints are not always trustworthy. Use byte sniffing
                    # as the primary classifier, and only use Telegram metadata as fallback or
                    # narrow disambiguation (e.g. MP4 audio-vs-video).
                    final_kind, final_mime = classify_media_from_bytes_and_hints(
                        media_bytes,
                        telegram_mime_type=hinted_mime,
                        telegram_kind_hint=object_kind_hint,
                        has_audio_attribute=has_audio_attribute,
                        has_sticker_attribute=has_sticker_attribute,
                    )

                    file_extension = get_file_extension_from_mime_or_bytes(final_mime, media_bytes) or ".jpg"
                    return {
                        "media_bytes": media_bytes,
                        "file_extension": file_extension,
                        "kind": final_kind,
                        "mime_type": final_mime,
                    }

                try:
                    downloaded = agent.execute(_download_from_telegram(agent.client), timeout=120.0)
                    if downloaded and downloaded.get("media_bytes"):
                        data["kind"] = downloaded.get("kind") or data.get("kind") or "photo"
                        data["mime_type"] = downloaded.get("mime_type") or data.get("mime_type")
                        media_cache_source.put(
                            unique_id,
                            data,
                            media_bytes=downloaded["media_bytes"],
                            file_extension=downloaded.get("file_extension") or ".jpg",
                        )
                        refreshed = media_cache_source.get_cached_record(unique_id) or data
                        data = refreshed
                        if refreshed.get("media_file"):
                            candidate = config_media_dir / refreshed["media_file"]
                            if candidate.exists() and candidate.is_file() and candidate.suffix.lower() != ".json":
                                media_file = candidate
                                logger.info(
                                    "Refresh-from-AI: downloaded and cached %s to %s",
                                    unique_id,
                                    candidate,
                                )
                except Exception as e:
                    logger.debug(
                        "Refresh-from-AI: Telegram download fallback failed for %s: %s",
                        unique_id,
                        e,
                    )
            
            if not media_file:
                return jsonify({"error": "Media file not found"}), 404
            
            # Reset the media description budget for this refresh request
            import config
            from media.media_budget import reset_description_budget
            reset_description_budget(config.MEDIA_DESC_BUDGET_PER_TICK)
            logger.info(
                f"Refresh-from-AI: reset budget to {config.MEDIA_DESC_BUDGET_PER_TICK} for {unique_id}"
            )
            
            # Create media chain for AI generation
            from media.media_source import (
                AIGeneratingMediaSource,
                AIChainMediaSource,
                CompositeMediaSource,
                UnsupportedFormatMediaSource,
                BudgetExhaustedMediaSource,
            )
            
            unsupported_source = UnsupportedFormatMediaSource()
            budget_source = BudgetExhaustedMediaSource()
            ai_source = AIGeneratingMediaSource(cache_directory=config_media_dir)
            
            media_chain = CompositeMediaSource(
                [
                    AIChainMediaSource(
                        cache_source=media_cache_source,
                        unsupported_source=unsupported_source,
                        budget_source=budget_source,
                        ai_source=ai_source,
                    )
                ]
            )
            
            # Determine media kind from MIME type
            from media.mime_utils import (
                get_mime_type_from_file_extension,
                is_tgs_mime_type,
                is_audio_mime_type,
                is_video_mime_type,
                is_image_mime_type,
            )
            
            mime_type = data.get("mime_type")
            if not mime_type:
                mime_type = get_mime_type_from_file_extension(media_file)
            
            if is_tgs_mime_type(mime_type):
                media_kind = "animated_sticker"
            elif mime_type == "audio/mp4":
                media_kind = "audio"
            elif is_audio_mime_type(mime_type):
                media_kind = "audio"
            elif is_video_mime_type(mime_type):
                media_kind = "video"
            elif is_image_mime_type(mime_type):
                media_kind = "photo"
            else:
                media_kind = data.get("kind", "photo")
            
            # Run the media chain in agent's event loop
            async def _refresh_coro(client: TelegramClient):
                logger.info(
                    "Refreshing AI description for %s using agent %s",
                    unique_id,
                    agent_config_name,
                )
                
                record = await media_chain.get(
                    unique_id=unique_id,
                    agent=agent,
                    doc=media_file,  # Path object
                    kind=media_kind,
                    sticker_set_name=data.get("sticker_set_name"),
                    sticker_name=data.get("sticker_name"),
                    sender_id=None,
                    sender_name=None,
                    channel_id=None,
                    channel_name=None,
                    media_ts=None,
                    duration=data.get("duration"),
                    mime_type=mime_type,
                    skip_fallback=True,
                )
                return record
            
            try:
                record = agent.execute(_refresh_coro(agent.client), timeout=120.0)
            except Exception as e:
                logger.error(f"Failed to refresh AI description: {e}")
                return jsonify({"error": f"Failed to refresh AI description: {e}"}), 500
            
            if record:
                new_description = record.get("description")
                new_status = record.get("status", "ok")
                logger.info(
                    "Got fresh AI description for %s (status=%s): %s",
                    unique_id,
                    new_status,
                    (new_description[:50] + "…") if new_description else "None",
                )
                
                # Return the new data like Media Editor does
                return jsonify({
                    "description": new_description,
                    "status": new_status,
                    "failure_reason": record.get("failure_reason")
                })
            else:
                return jsonify({"error": "Failed to generate description"}), 500
            
        except Exception as e:
            logger.error(f"Error refreshing description for {unique_id}: {e}")
            import traceback
            traceback.print_exc()
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
        except ValueError as e:
            logger.warning(
                "Profile photo request rejected for %s/%s: %s",
                agent_config_name,
                unique_id,
                e,
            )
            return jsonify({"error": str(e)}), 400
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
            
            # Return raw media bytes with a MIME type derived from content.
            # The previous hardcoded image/jpeg breaks sticker/webp/video previews.
            mime_type = detect_mime_type_from_bytes(media_bytes[:1024]) if media_bytes else "application/octet-stream"
            if mime_type == "application/gzip":
                # Telegram animated stickers (TGS) are gzip-compressed Lottie data.
                mime_type = "application/x-tgsticker"
            return send_file(
                BytesIO(media_bytes),
                mimetype=mime_type,
                as_attachment=False
            )
            
        except Exception as e:
            logger.error(f"Error getting media file for {unique_id}: {e}")
            return jsonify({"error": str(e)}), 500
