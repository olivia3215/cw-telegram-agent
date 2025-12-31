# translation_cache.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
Translation cache using MySQL backend.
"""

import logging

logger = logging.getLogger(__name__)


def get_translation(message: str) -> str | None:
    """
    Get translation for a message.
    
    Args:
        message: The original message text
        
    Returns:
        Translation text, or None if translation is same as message or not found
    """
    try:
        from db import translations as db_translations
        translation = db_translations.get_translation(message)
        if translation:
            # Update last_used timestamp
            db_translations.update_last_used(message)
        return translation
    except Exception as e:
        logger.error(f"Failed to get translation from MySQL: {e}")
        return None


def save_translation(message: str, translation: str | None) -> None:
    """
    Save a translation.
    
    Args:
        message: The original message text
        translation: The translation, or None if translation is same as message
    """
    try:
        from db import translations as db_translations
        db_translations.save_translation(message, translation)
    except Exception as e:
        logger.error(f"Failed to save translation to MySQL: {e}")
        raise

