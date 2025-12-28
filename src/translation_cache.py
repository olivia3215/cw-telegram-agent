# translation_cache.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Translation cache abstraction supporting both filesystem and MySQL backends.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from config import STATE_DIRECTORY, STORAGE_BACKEND

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from db import translations as db_translations

TRANSLATION_CACHE_EXPIRY_DAYS = 10


def get_translation(message: str) -> str | None:
    """
    Get translation for a message.
    
    Args:
        message: The original message text
        
    Returns:
        Translation text, or None if translation is same as message or not found
    """
    if STORAGE_BACKEND == "mysql":
        try:
            from db import translations as db_translations
            translation = db_translations.get_translation(message)
            if translation:
                # Update last_used timestamp
                db_translations.update_last_used(message)
            return translation
        except Exception as e:
            logger.warning(f"Failed to get translation from MySQL, falling back to filesystem: {e}")
            # Fall through to filesystem
    
    # Filesystem fallback
    return _get_translation_filesystem(message)


def save_translation(message: str, translation: str | None) -> None:
    """
    Save a translation.
    
    Args:
        message: The original message text
        translation: The translation, or None if translation is same as message
    """
    if STORAGE_BACKEND == "mysql":
        try:
            from db import translations as db_translations
            db_translations.save_translation(message, translation)
            return
        except Exception as e:
            logger.warning(f"Failed to save translation to MySQL, falling back to filesystem: {e}")
            # Fall through to filesystem
    
    # Filesystem fallback
    _save_translation_filesystem(message, translation)


def _get_translation_filesystem(message: str) -> str | None:
    """Get translation from filesystem cache."""
    cache_path = Path(STATE_DIRECTORY) / "translations.json"
    if not cache_path.exists():
        return None
    
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        
        if message in cache:
            translation_data = cache[message]
            translated_text = translation_data.get("translated_text")
            # Check if expired
            timestamp_str = translation_data.get("timestamp")
            if timestamp_str:
                try:
                    timestamp = datetime.fromisoformat(timestamp_str)
                    expiry_threshold = datetime.now() - timedelta(days=TRANSLATION_CACHE_EXPIRY_DAYS)
                    if timestamp < expiry_threshold:
                        return None  # Expired
                except (ValueError, TypeError):
                    pass
            return translated_text
    except Exception as e:
        logger.warning(f"Failed to load translation from filesystem: {e}")
    
    return None


def _save_translation_filesystem(message: str, translation: str | None) -> None:
    """Save translation to filesystem cache."""
    cache_path = Path(STATE_DIRECTORY) / "translations.json"
    
    try:
        # Load existing cache
        cache = {}
        if cache_path.exists():
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    cache = json.load(f)
            except Exception:
                pass
        
        # Update cache
        cache[message] = {
            "translated_text": translation,
            "timestamp": datetime.now().isoformat(),
        }
        
        # Filter expired entries
        now = datetime.now()
        expiry_threshold = now - timedelta(days=TRANSLATION_CACHE_EXPIRY_DAYS)
        filtered_cache = {}
        for text, translation_data in cache.items():
            timestamp_str = translation_data.get("timestamp")
            if timestamp_str:
                try:
                    timestamp = datetime.fromisoformat(timestamp_str)
                    if timestamp >= expiry_threshold:
                        filtered_cache[text] = translation_data
                except (ValueError, TypeError):
                    # Keep entries with invalid timestamps
                    filtered_cache[text] = translation_data
        
        # Save cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(filtered_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save translation to filesystem: {e}")

