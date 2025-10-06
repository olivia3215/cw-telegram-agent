# media/media_types.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from dataclasses import dataclass
from typing import Any, Literal


@dataclass
class MediaItem:
    kind: Literal[
        "photo", "sticker", "gif", "png", "animation", "video", "animated_sticker"
    ]
    unique_id: str  # REQUIRED stable ID (e.g., Telegram file_unique_id)
    mime: str | None = None
    sticker_set_name: str | None = None
    sticker_name: str | None = None
    file_ref: Any | None = None  # opaque handle for future download code
    sticker_set_id: int | None = None
    sticker_access_hash: int | None = None
