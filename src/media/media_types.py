# media/media_types.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .mime_utils import is_tgs_mime_type


class MediaKind(str, Enum):
    """
    Semantic category of media.

    - PHOTO: Static images
    - STICKER: Stickers (includes both static webp and animated TGS)
    - GIF: GIF animations
    - ANIMATION: Telegram animations (typically MP4)
    - VIDEO: Video files
    - AUDIO: Audio files
    - DOCUMENT: Generic document files (e.g., markdown, PDF, text files)
    """

    PHOTO = "photo"
    STICKER = "sticker"
    GIF = "gif"
    ANIMATION = "animation"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"


@dataclass
class MediaItem:
    """
    Represents a media item from Telegram.

    The `kind` field indicates the semantic category (photo, sticker, video, etc.)
    The `mime` field indicates the technical format (image/webp, application/gzip, video/mp4, etc.)

    Use helper methods to check specific media characteristics:
    - is_animated_sticker(): TGS animated stickers
    - needs_video_analysis(): Media that should use video description API
    - is_sticker(): Any sticker (static or animated)
    """

    kind: MediaKind
    unique_id: str  # REQUIRED stable ID (e.g., Telegram file_unique_id)
    mime: str | None = None
    sticker_set_name: str | None = None
    sticker_set_title: str | None = None
    sticker_name: str | None = None
    file_ref: Any | None = None  # opaque handle for future download code
    sticker_set_id: int | None = None
    sticker_access_hash: int | None = None
    duration: int | None = None  # video/animation duration in seconds

    def is_animated_sticker(self) -> bool:
        """Check if this is a TGS animated sticker (sticker with gzip MIME type)."""
        return (
            self.kind == MediaKind.STICKER
            and self.mime is not None
            and is_tgs_mime_type(self.mime)
        )

    def is_sticker(self) -> bool:
        """Check if this is any type of sticker (static or animated)."""
        return self.kind == MediaKind.STICKER

    def needs_video_analysis(self) -> bool:
        """Check if this media should use video description API (videos and animated stickers)."""
        return (
            self.kind in (MediaKind.VIDEO, MediaKind.ANIMATION)
            or self.is_animated_sticker()
        )

    def is_video(self) -> bool:
        """Check if this is a video or animation."""
        return self.kind in (MediaKind.VIDEO, MediaKind.ANIMATION)

    def is_audio(self) -> bool:
        """Check if this is an audio file."""
        return self.kind == MediaKind.AUDIO

    def is_voice_message(self) -> bool:
        """Check if this is a voice message (audio from msg.voice)."""
        return (
            self.kind == MediaKind.AUDIO
            and hasattr(self.file_ref, "__class__")
            and "Voice" in str(self.file_ref.__class__)
        )

    def needs_voice_analysis(self) -> bool:
        """Check if this media should use voice message analysis."""
        return self.is_voice_message()

    def is_document(self) -> bool:
        """Check if this is a document file."""
        return self.kind == MediaKind.DOCUMENT
