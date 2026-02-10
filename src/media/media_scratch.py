# src/media/media_scratch.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import logging
import os
import shutil
from pathlib import Path

from config import MEDIA_SCRATCH_DIRECTORY

logger = logging.getLogger(__name__)

def init_media_scratch():
    """
    Initialize the media scratch directory.
    Clears any existing files to ensure a clean state on startup.
    """
    scratch_dir = Path(MEDIA_SCRATCH_DIRECTORY)
    
    try:
        if scratch_dir.exists():
            logger.info(f"Clearing media scratch directory: {scratch_dir}")
            # Use shutil.rmtree to remove directory and its contents
            shutil.rmtree(scratch_dir)
        
        # Create fresh directory
        scratch_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Initialized media scratch directory: {scratch_dir}")
    except Exception as e:
        logger.error(f"Failed to initialize media scratch directory: {e}")

def get_scratch_file(filename: str) -> Path:
    """
    Get a path for a file in the scratch directory.
    Ensures the scratch directory exists.
    """
    scratch_dir = Path(MEDIA_SCRATCH_DIRECTORY)
    if not scratch_dir.exists():
        scratch_dir.mkdir(parents=True, exist_ok=True)
    
    return scratch_dir / filename

