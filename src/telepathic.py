# telepathic.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
from pathlib import Path
from typing import Set

from config import CONFIG_DIRECTORIES

logger = logging.getLogger(__name__)

# Cache for telepathic channel IDs
_telepathic_channels: Set[int] = set()
_telepathic_cache_loaded = False


def _load_telepathic_channels() -> Set[int]:
    """
    Load telepathic channel IDs from all configuration directories.
    
    Looks for Telepaths.md files in each config directory and parses
    lines of the form "- {number}" where number is a Telegram channel/group/user ID.
    
    Returns:
        Set of channel IDs that are telepathic
    """
    telepathic_channels = set()
    
    for config_dir in CONFIG_DIRECTORIES:
        config_path = Path(config_dir)
        if not config_path.exists() or not config_path.is_dir():
            logger.warning(f"Config directory does not exist: {config_dir}")
            continue
            
        telepaths_file = config_path / "Telepaths.md"
        if not telepaths_file.exists():
            logger.debug(f"No Telepaths.md file found in {config_dir}")
            continue
            
        try:
            content = telepaths_file.read_text()
            logger.info(f"Loading telepathic channels from {telepaths_file}")
            
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("- "):
                    # Extract the number after "- "
                    number_str = line[2:].strip()
                    try:
                        channel_id = int(number_str)
                        telepathic_channels.add(channel_id)
                        logger.debug(f"Added telepathic channel: {channel_id}")
                    except ValueError:
                        logger.warning(f"Invalid channel ID in Telepaths.md: {number_str}")
                        
        except Exception as e:
            logger.error(f"Failed to read Telepaths.md from {config_dir}: {e}")
            
    logger.info(f"Loaded {len(telepathic_channels)} telepathic channels")
    return telepathic_channels


def is_telepath(channel_id: int) -> bool:
    """
    Check if a channel is telepathic (agent thoughts are visible to participants).
    
    Args:
        channel_id: Telegram channel/group/user ID
        
    Returns:
        True if the channel is telepathic, False otherwise
    """
    global _telepathic_channels, _telepathic_cache_loaded
    
    if not _telepathic_cache_loaded:
        _telepathic_channels = _load_telepathic_channels()
        _telepathic_cache_loaded = True
        
    return channel_id in _telepathic_channels


def reload_telepathic_channels():
    """
    Reload telepathic channels from configuration files.
    Useful for testing or when configuration changes.
    """
    global _telepathic_channels, _telepathic_cache_loaded
    
    _telepathic_channels = _load_telepathic_channels()
    _telepathic_cache_loaded = True
    logger.info("Reloaded telepathic channels configuration")
