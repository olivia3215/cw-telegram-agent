# admin_console/sticker_import.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Sticker import functionality for the admin console.

This module handles importing stickers from Telegram sticker sets into
the media directory for use by agents.
"""

import logging
from pathlib import Path
from typing import Any

from telethon import TelegramClient  # pyright: ignore[reportMissingImports]
from telethon.tl.functions.messages import GetStickerSetRequest  # pyright: ignore[reportMissingImports]
from telethon.tl.types import (  # pyright: ignore[reportMissingImports]
    InputStickerSetShortName,
)

from clock import clock
from config import STATE_DIRECTORY
from datetime import UTC
from media.media_sources import get_directory_media_source
from media.mime_utils import detect_mime_type_from_bytes, is_tgs_mime_type
from telegram_download import download_media_bytes
from telegram_media import get_unique_id

logger = logging.getLogger(__name__)


async def _validate_sticker_import(
    client: TelegramClient, sticker_set_name: str
) -> dict[str, Any]:
    """
    Validate that the sticker set can be imported.
    
    Args:
        client: Telegram client to use for API calls
        sticker_set_name: Short name of the sticker set to import
        
    Returns:
        Dictionary with validation result:
        - If valid: {"valid": True, "result": <sticker_set_result>}
        - If invalid: {"valid": False, "error": <error_message>}
    """
    # Check if the client is authenticated before proceeding
    try:
        if not await client.is_user_authorized():
            logger.error("Puppet master is not authenticated to Telegram.")
            return {
                "valid": False,
                "error": "Puppet master is not authenticated to Telegram. Run './telegram_login.sh --puppet-master' to log in.",
            }
    except Exception as e:
        logger.error(f"Failed to connect puppet master client: {e}")
        return {
            "valid": False,
            "error": f"Failed to connect puppet master client: {e}",
        }
    
    # Download sticker set using the same pattern as run.py
    logger.info(f"Requesting sticker set: {sticker_set_name}")
    
    # Try with InputStickerSetShortName first
    try:
        logger.info(
            f"Attempting to get sticker set with short name: '{sticker_set_name}'"
        )
        result = await client(
            GetStickerSetRequest(
                stickerset=InputStickerSetShortName(short_name=sticker_set_name),
                hash=0,
            )
        )
        logger.info(
            f"Successfully got sticker set result with short name: {type(result)}"
        )
        return {"valid": True, "result": result}
    except Exception as e:
        logger.warning(
            f"Failed to get sticker set with short name '{sticker_set_name}': {e}"
        )
        logger.warning(f"Exception type: {type(e)}")
        
        # Try case variations for debugging
        if sticker_set_name != sticker_set_name.lower():
            logger.info(f"Trying lowercase version: '{sticker_set_name.lower()}'")
            try:
                result = await client(
                    GetStickerSetRequest(
                        stickerset=InputStickerSetShortName(
                            short_name=sticker_set_name.lower()
                        ),
                        hash=0,
                    )
                )
                logger.info(
                    f"Successfully got sticker set result with lowercase name: {type(result)}"
                )
                return {"valid": True, "result": result}
            except Exception as e2:
                logger.warning(f"Lowercase version also failed: {e2}")
        
        # Re-raise with more context but avoid double-wrapping
        error_msg = str(e)
        if "not registered in the system" in error_msg:
            return {
                "valid": False,
                "error": (
                    f"Sticker set '{sticker_set_name}' appears to be private or restricted. "
                    f"Private sticker sets cannot be imported via the API. "
                    f"If you have access to this sticker set, you may need to add the stickers manually "
                    f"or use a different import method. Original error: {error_msg}"
                ),
            }
        else:
            return {
                "valid": False,
                "error": (
                    f"Sticker set '{sticker_set_name}' not found or not accessible. "
                    f"Make sure the sticker set exists and is public. "
                    f"Original error: {error_msg}"
                ),
            }


def _extract_sticker_set_metadata(result: Any) -> tuple[str | None, bool]:
    """
    Extract sticker set title and emoji set status from API result.
    
    Args:
        result: Sticker set API result
        
    Returns:
        Tuple of (sticker_set_title, is_emoji_set)
    """
    sticker_set_title = None
    is_emoji_set_for_import = False
    
    if hasattr(result, "set"):
        set_obj = result.set
        sticker_set_title = getattr(set_obj, "title", None)
        if not sticker_set_title:
            # Fallback to short_name if title is not available
            sticker_set_title = getattr(set_obj, "short_name", None)
        
        # Determine if this is an emoji set
        if hasattr(set_obj, "emojis") and getattr(set_obj, "emojis", False):
            is_emoji_set_for_import = True
        # Check set type attribute if available
        set_type = getattr(set_obj, "set_type", None)
        if set_type:
            type_str = str(set_type)
            if "emoji" in type_str.lower() or "Emoji" in type_str:
                is_emoji_set_for_import = True
    
    return sticker_set_title, is_emoji_set_for_import


async def _process_sticker_import_batch(
    client: TelegramClient,
    documents: list[Any],
    sticker_set_name: str,
    sticker_set_title: str | None,
    is_emoji_set: bool,
    target_dir: Path,
    cache_source: Any,
) -> tuple[int, int]:
    """
    Process a batch of sticker documents for import.
    
    Args:
        client: Telegram client for downloading media
        documents: List of document objects from sticker set
        sticker_set_name: Short name of the sticker set
        sticker_set_title: Long name (title) of the sticker set
        is_emoji_set: Whether this is an emoji set
        target_dir: Target directory for imported stickers
        cache_source: DirectoryMediaSource instance for saving
        
    Returns:
        Tuple of (imported_count, skipped_count)
    """
    imported_count = 0
    skipped_count = 0
    
    for doc in documents:
        try:
            # Get sticker name using the same pattern as run.py
            sticker_name = next(
                (a.alt for a in doc.attributes if hasattr(a, "alt")),
                f"sticker_{imported_count + skipped_count + 1}",
            )
            
            # Get unique ID and other metadata
            unique_id = get_unique_id(doc)
            
            if not unique_id:
                logger.warning(
                    f"No unique ID found for sticker in set {sticker_set_name}"
                )
                continue
            
            # Check if already exists
            existing_file = target_dir / f"{unique_id}.json"
            if existing_file.exists():
                skipped_count += 1
                continue
            
            # Download the media file
            try:
                media_bytes = await download_media_bytes(client, doc)
                
                # Determine file extension
                mime_type = getattr(doc, "mime_type", None)
                if is_tgs_mime_type(mime_type):
                    file_ext = ".tgs"
                elif mime_type == "image/webp":
                    file_ext = ".webp"
                elif mime_type == "image/png":
                    file_ext = ".png"
                else:
                    file_ext = ".webp"
                
                # Detect actual MIME type from file bytes for accurate processing
                detected_mime_type = detect_mime_type_from_bytes(media_bytes)
                
                # All stickers use kind="sticker" (MIME type distinguishes static vs animated)
                media_kind = "sticker"
                
                # Create JSON record with empty description (AI will be used on-demand via refresh)
                media_record = {
                    "unique_id": unique_id,
                    "kind": media_kind,
                    "sticker_set_name": sticker_set_name,
                    "sticker_set_title": sticker_set_title,  # Store long name (title)
                    "sticker_name": sticker_name,
                    "is_emoji_set": is_emoji_set,  # Use value from set query
                    "description": None,  # Leave empty, use AI refresh when needed
                    "status": "pending_description",
                    "ts": clock.now(UTC).isoformat(),
                    "mime_type": detected_mime_type,  # Use detected MIME type, not Telegram's
                }
                
                # Save both media file and JSON record using DirectoryMediaSource
                cache_source.put(unique_id, media_record, media_bytes, file_ext)
                
                imported_count += 1
                
            except Exception as e:
                logger.error(f"Failed to download sticker {unique_id}: {e}")
                # Determine correct kind for error record
                telegram_mime = getattr(doc, "mime_type", None)
                # All stickers use kind="sticker" (MIME type distinguishes static vs animated)
                error_kind = "sticker"
                
                # Create error record
                error_record = {
                    "unique_id": unique_id,
                    "kind": error_kind,
                    "sticker_set_name": sticker_set_name,
                    "sticker_set_title": sticker_set_title,  # Store long name (title)
                    "sticker_name": sticker_name,
                    "is_emoji_set": is_emoji_set,  # Use value from set query
                    "description": None,
                    "status": "error",
                    "failure_reason": f"Download failed: {str(e)}",
                    "ts": clock.now(UTC).isoformat(),
                    "mime_type": telegram_mime,
                }
                # Save error record using DirectoryMediaSource (no media file for errors)
                cache_source.put(unique_id, error_record, None, None)
                imported_count += 1
                
        except Exception as e:
            logger.error(f"Error processing sticker in set {sticker_set_name}: {e}")
            continue
    
    return imported_count, skipped_count


async def import_sticker_set_async(
    client: TelegramClient, sticker_set_name: str, target_directory: str
) -> dict[str, Any]:
    """
    Import all stickers from a Telegram sticker set.
    
    This function orchestrates the full import process:
    1. Validates client authentication and sticker set accessibility
    2. Fetches sticker set metadata
    3. Processes all stickers in the set
    4. Saves stickers and metadata to the target directory
    
    Args:
        client: Telegram client to use for API calls
        sticker_set_name: Short name of the sticker set to import
        target_directory: Directory path where stickers should be saved
        
    Returns:
        Dictionary with import result:
        - On success: {"success": True, "imported_count": int, "skipped_count": int, "message": str}
        - On failure: {"success": False, "error": str}
    """
    logger.info(f"Starting sticker import for set: {sticker_set_name}")
    logger.info(f"Target directory: {target_directory}")
    
    # Validate import
    validation_result = await _validate_sticker_import(client, sticker_set_name)
    if not validation_result["valid"]:
        return {
            "success": False,
            "error": validation_result["error"],
        }
    
    result = validation_result["result"]
    
    # Set up target directory
    target_dir = Path(target_directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # Create a single DirectoryMediaSource instance outside the loop to enable in-memory caching
    cache_source = get_directory_media_source(target_dir)
    
    try:
        # Extract sticker set metadata
        sticker_set_title, is_emoji_set_for_import = _extract_sticker_set_metadata(result)
        
        logger.info(
            f"Sticker set '{sticker_set_name}' has title: {sticker_set_title}, is_emoji_set: {is_emoji_set_for_import}"
        )
        
        # Process each document using the same pattern as run.py
        # Convert result.documents to list to handle dict_values case
        logger.info(f"result.documents type: {type(result.documents)}")
        try:
            documents = list(result.documents)
            logger.info(
                f"Successfully converted to list with {len(documents)} documents"
            )
        except Exception as e:
            logger.error(f"Failed to convert result.documents to list: {e}")
            return {
                "success": False,
                "error": f"Failed to process sticker set documents: {e}",
            }
        
        # Process the batch
        imported_count, skipped_count = await _process_sticker_import_batch(
            client,
            documents,
            sticker_set_name,
            sticker_set_title,
            is_emoji_set_for_import,
            target_dir,
            cache_source,
        )
        
    except Exception as e:
        logger.error(f"Error importing sticker set {sticker_set_name}: {e}")
        return {"success": False, "error": str(e)}
    
    return {
        "success": True,
        "imported_count": imported_count,
        "skipped_count": skipped_count,
        "message": f"Imported {imported_count} stickers, skipped {skipped_count} existing ones",
    }

