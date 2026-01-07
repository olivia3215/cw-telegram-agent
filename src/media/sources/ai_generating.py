# media/sources/ai_generating.py
#
# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

"""
AI-generating media source.

Generates media descriptions using AI.
This source always succeeds (never returns None). It either:
1. Successfully generates a description
2. Returns a transient failure record (timeouts, etc.)
3. Returns a permanent failure record (LLM errors, etc.)

Caching is handled by the calling AIChainMediaSource.
"""

import logging
import time
from datetime import UTC
from pathlib import Path
from typing import Any

import httpx  # pyright: ignore[reportMissingImports]

from clock import clock
from llm.exceptions import RetryableLLMError
from llm.media_helper import get_media_llm
from telegram_download import download_media_bytes

from ..media_scratch import get_scratch_file
from ..mime_utils import (
    detect_mime_type_from_bytes,
    get_file_extension_from_mime_or_bytes,
    get_mime_type_from_file_extension,
    is_audio_mime_type,
    is_tgs_mime_type,
    normalize_mime_type,
)
from .base import MediaSource, MediaStatus, MEDIA_FILE_EXTENSIONS, _needs_video_analysis, get_describe_timeout_secs
from .helpers import make_error_record

logger = logging.getLogger(__name__)

class AIGeneratingMediaSource(MediaSource):
    """
    Generates media descriptions using AI.

    This source always succeeds (never returns None). It either:
    1. Successfully generates a description
    2. Returns a transient failure record (timeouts, etc.)
    3. Returns a permanent failure record (LLM errors, etc.)

    Caching is handled by the calling AIChainMediaSource.
    """

    def __init__(self, cache_directory: Path):
        """
        Initialize the AI generating source.

        Args:
            cache_directory: Directory for debug saves (no longer used for caching)
        """
        self.cache_directory = Path(cache_directory)
        self.cache_directory.mkdir(parents=True, exist_ok=True)

    async def get(
        self,
        unique_id: str,
        agent: Any = None,
        doc: Any = None,
        kind: str | None = None,
        sticker_set_name: str | None = None,
        sticker_name: str | None = None,
        **metadata,
    ) -> dict[str, Any] | None:
        """
        Generate a media description using AI.

        Returns a dict with description or error record, or None if doc is not available.
        Caches successful results and unsupported formats to disk.
        """

        if agent is None:
            raise ValueError("AIGeneratingMediaSource: agent is required but was None")

        if doc is None:
            # Return None when doc is not available - we cannot generate without it
            # This allows callers that are only reading from cache (like format_message_for_prompt)
            # to work gracefully. The description can be generated later when doc is available.
            return None

        client = getattr(agent, "client", None)
        if not client:
            raise ValueError(
                f"AIGeneratingMediaSource: agent missing client for {unique_id}"
            )

        # Use the media LLM for descriptions (from MEDIA_MODEL), not the agent's LLM
        media_llm = get_media_llm()

        t0 = time.perf_counter()

        # If doc is a Path, try to get MIME type from file extension first
        # This helps avoid application/octet-stream fallback for valid media files
        # Special handling for .m4a files - they should be audio/mp4, not video/mp4
        if hasattr(doc, "suffix") and hasattr(doc, "read_bytes"):
            # doc is a Path object
            if doc.suffix.lower() == ".m4a":
                # M4A files are audio-only MP4 containers
                metadata["mime_type"] = normalize_mime_type("audio/mp4")
            else:
                mime_from_ext = get_mime_type_from_file_extension(doc)
                if mime_from_ext:
                    detected_mime_type = normalize_mime_type(mime_from_ext)
                    if detected_mime_type:
                        metadata["mime_type"] = detected_mime_type

        # Check if media file already exists in cache before downloading
        data: bytes | None = None
        for ext in MEDIA_FILE_EXTENSIONS:
            cached_file = self.cache_directory / f"{unique_id}{ext}"
            if cached_file.exists():
                try:
                    data = cached_file.read_bytes()
                    logger.debug(
                        f"AIGeneratingMediaSource: using cached media file for {unique_id} from {cached_file}"
                    )
                    break
                except Exception as e:
                    logger.warning(
                        f"AIGeneratingMediaSource: failed to read cached file {cached_file}: {e}, will download instead"
                    )
                    data = None
        
        # Download media bytes only if not found in cache
        if data is None:
            try:
                data = await download_media_bytes(client, doc)
            except Exception as e:
                logger.exception(
                    f"AIGeneratingMediaSource: download failed for {unique_id}: {e}"
                )
                # Transient failure - don't cache to disk
                return make_error_record(
                    unique_id,
                    MediaStatus.TEMPORARY_FAILURE,
                    f"download failed: {str(e)[:100]}",
                    retryable=True,
                    kind=kind,
                    sticker_set_name=sticker_set_name,
                    sticker_name=sticker_name,
                    agent=agent,
                    **metadata,
                )
        dl_ms = (time.perf_counter() - t0) * 1000

        # MIME type check is now handled by UnsupportedFormatMediaSource earlier in pipeline
        # Detect MIME type before LLM call so it's available in exception handlers
        # Use byte detection to verify/override extension-based detection
        # BUT preserve audio/mp4 for .m4a files (byte detection can't distinguish M4A from MP4 video)
        detected_mime_type = normalize_mime_type(detect_mime_type_from_bytes(data))
        
        # If this is a .m4a file, preserve audio/mp4 even if byte detection says video/mp4
        # (byte detection can't distinguish M4A from MP4 video - they have the same container signature)
        is_m4a_file = hasattr(doc, "suffix") and doc.suffix.lower() == ".m4a"
        if is_m4a_file:
            # Force audio/mp4 for M4A files regardless of byte detection
            metadata["mime_type"] = normalize_mime_type("audio/mp4")
            detected_mime_type = normalize_mime_type("audio/mp4")
        elif detected_mime_type and detected_mime_type != "application/octet-stream":
            # Prefer byte detection over extension-based detection (more accurate)
            metadata["mime_type"] = detected_mime_type
        elif detected_mime_type == "application/octet-stream" and "mime_type" in metadata:
            # Keep extension-based MIME type if byte detection fails
            # (byte detection can fail for some valid files)
            # Use the extension-based MIME type for LLM calls
            detected_mime_type = metadata["mime_type"]
        elif detected_mime_type:
            metadata["mime_type"] = detected_mime_type
        
        # Ensure we have a MIME type for LLM calls (use metadata if available, otherwise detected)
        final_mime_type = metadata.get("mime_type") or detected_mime_type
        if final_mime_type:
            metadata["mime_type"] = final_mime_type

        # For TGS files (animated stickers), convert to video first
        video_file_path = None
        is_converted_tgs = False
        if is_tgs_mime_type(final_mime_type):
            try:
                from ..tgs_converter import convert_tgs_to_video

                # Save TGS data to scratch file
                tgs_path = get_scratch_file(f"{unique_id}.tgs")
                tgs_path.write_bytes(data)

                # Convert TGS to video
                # Use 4 fps for efficiency - AI samples key frames anyway
                video_file_path = convert_tgs_to_video(
                    tgs_path,
                    tgs_path.with_suffix(".mp4"),
                    width=512,
                    height=512,
                    duration=metadata.get("duration"),
                    target_fps=4.0,
                )

                # Read the video data
                data = video_file_path.read_bytes()

                # Preserve original TGS mime_type before updating (needed for fallback descriptions)
                # Store the original TGS mime_type - this is what the actual file on disk is
                original_tgs_mime = final_mime_type
                metadata["original_mime_type"] = original_tgs_mime

                # Update final_mime_type to video/mp4 for LLM processing (converted file)
                # But keep the original TGS mime_type in metadata since that's what's on disk
                detected_mime_type = normalize_mime_type("video/mp4")
                final_mime_type = detected_mime_type  # Use video/mp4 for LLM call
                # Don't overwrite metadata["mime_type"] - keep original TGS mime_type for saved record
                is_converted_tgs = True

                logger.info(
                    f"Converted TGS to video for {unique_id}: {len(data)} bytes"
                )

            except Exception as e:
                logger.error(f"TGS to video conversion failed for {unique_id}: {e}")
                # Clean up temporary files
                if "tgs_path" in locals() and tgs_path and tgs_path.exists():
                    tgs_path.unlink()
                if video_file_path and video_file_path.exists():
                    video_file_path.unlink()
                # Return error
                return make_error_record(
                    unique_id,
                    MediaStatus.PERMANENT_FAILURE,
                    f"TGS conversion failed: {str(e)[:100]}",
                    kind=kind,
                    sticker_set_name=sticker_set_name,
                    sticker_name=sticker_name,
                    agent=agent,
                    **metadata,
                )

        # Call LLM to generate description (choose method based on media kind)
        try:
            t1 = time.perf_counter()

            # Use describe_video for:
            # - Media that needs video analysis (videos, animations)
            # - Converted TGS files (now in video format)
            if _needs_video_analysis(kind, final_mime_type) or is_converted_tgs:
                duration = metadata.get("duration")
                desc = await media_llm.describe_video(
                    data,
                    final_mime_type,
                    duration=duration,
                    timeout_s=get_describe_timeout_secs(),
                )
            elif kind == "audio" or is_audio_mime_type(final_mime_type):
                # Audio files (including voice messages)
                # Route to describe_audio if kind is audio OR MIME type indicates audio
                if hasattr(media_llm, "is_audio_mime_type_supported"):
                    # Check if MIME type is supported (if we have one)
                    # If no MIME type or application/octet-stream, pass None and let describe_audio detect it
                    audio_mime_type = final_mime_type if final_mime_type and final_mime_type != "application/octet-stream" else None
                    
                    # Only check support if we have a specific MIME type
                    if audio_mime_type and not media_llm.is_audio_mime_type_supported(audio_mime_type):
                        # Audio MIME type not supported - this shouldn't happen for valid audio, but handle gracefully
                        logger.warning(
                            f"AIGeneratingMediaSource: audio MIME type {audio_mime_type} not supported for {unique_id}, "
                            f"but kind={kind} indicates audio. Attempting describe_audio anyway."
                        )
                    
                    duration = metadata.get("duration")
                    desc = await media_llm.describe_audio(
                        data,
                        audio_mime_type,  # Will be None if not available, describe_audio will detect from bytes
                        duration=duration,
                        timeout_s=get_describe_timeout_secs(),
                    )
                else:
                    # LLM doesn't support audio description - this shouldn't happen, but fall through to describe_image
                    logger.warning(
                        f"AIGeneratingMediaSource: kind={kind} indicates audio but LLM doesn't support audio description for {unique_id}"
                    )
                    # Fall through to describe_image which will raise ValueError
                    desc = await media_llm.describe_image(
                        data, None, timeout_s=get_describe_timeout_secs()
                    )
            else:
                # Ensure we have a valid MIME type before calling describe_image
                # If final_mime_type is None or invalid, let describe_image detect it from bytes
                image_mime_type = final_mime_type if final_mime_type and final_mime_type != "application/octet-stream" else None
                logger.debug(
                    f"AIGeneratingMediaSource: calling describe_image for {unique_id} with MIME type: {image_mime_type} "
                    f"(final_mime_type={final_mime_type}, detected={detected_mime_type}, from_ext={'mime_type' in metadata})"
                )
                desc = await media_llm.describe_image(
                    data, image_mime_type, timeout_s=get_describe_timeout_secs()
                )
            desc = (desc or "").strip()
        except httpx.TimeoutException:
            logger.debug(
                f"AIGeneratingMediaSource: timeout after {get_describe_timeout_secs()}s for {unique_id}"
            )

            # Clean up temporary TGS and video files if conversion succeeded
            if is_converted_tgs:
                if video_file_path and video_file_path.exists():
                    video_file_path.unlink()
                if "tgs_path" in locals() and tgs_path and tgs_path.exists():
                    tgs_path.unlink()

            # Transient failure - return error record for AIChainMediaSource to handle
            return make_error_record(
                unique_id,
                MediaStatus.TEMPORARY_FAILURE,
                f"timeout after {get_describe_timeout_secs()}s",
                retryable=True,
                kind=kind,
                sticker_set_name=sticker_set_name,
                sticker_name=sticker_name,
                agent=agent,
                **metadata,
            )
        except ValueError as e:
            # ValueError is raised for unsupported formats or videos that are too long
            # These are permanent failures
            logger.info(
                f"AIGeneratingMediaSource: format check failed for {unique_id}: {e}"
            )

            # Clean up temporary TGS and video files if conversion succeeded
            if is_converted_tgs:
                if video_file_path and video_file_path.exists():
                    video_file_path.unlink()
                if "tgs_path" in locals() and tgs_path and tgs_path.exists():
                    tgs_path.unlink()

            # Permanent failure - return error record for AIChainMediaSource to handle
            return make_error_record(
                unique_id,
                MediaStatus.UNSUPPORTED,
                str(e),
                kind=kind,
                sticker_set_name=sticker_set_name,
                sticker_name=sticker_name,
                agent=agent,
                **metadata,
            )
        except RuntimeError as e:
            # RuntimeError is raised for API errors (400, 500, etc.)
            # Log the error with MIME type and file size info for debugging
            file_size_mb = len(data) / (1024 * 1024) if data else 0
            logger.error(
                f"AIGeneratingMediaSource: LLM failed for {unique_id}: {e} "
                f"(MIME type: {final_mime_type}, detected: {detected_mime_type}, "
                f"file size: {file_size_mb:.2f}MB, kind: {kind})"
            )
            
            # Clean up temporary TGS and video files if conversion succeeded
            if is_converted_tgs:
                if video_file_path and video_file_path.exists():
                    video_file_path.unlink()
                if "tgs_path" in locals() and tgs_path and tgs_path.exists():
                    tgs_path.unlink()
            
            # Check if error is explicitly marked as non-retryable (e.g., 401, 403, 404, 501)
            # This flag is set by gemini.py for permanent HTTP errors
            if hasattr(e, "is_retryable") and e.is_retryable is False:
                # Permanent failure - explicitly marked as non-retryable
                error_str = str(e)
                return make_error_record(
                    unique_id,
                    MediaStatus.PERMANENT_FAILURE,
                    f"LLM API error: {error_str[:100]}",
                    kind=kind,
                    sticker_set_name=sticker_set_name,
                    sticker_name=sticker_name,
                    agent=agent,
                    **metadata,
                )
            
            # Check if this is a format/argument error (400) - treat as permanent failure
            error_str = str(e)
            if "400" in error_str or "INVALID_ARGUMENT" in error_str:
                # For 400 errors, provide more context about what might be wrong
                failure_reason = f"LLM API error (400): {error_str[:100]}"
                if file_size_mb > 20:
                    failure_reason += f" (file may be too large: {file_size_mb:.1f}MB)"
                return make_error_record(
                    unique_id,
                    MediaStatus.PERMANENT_FAILURE,
                    failure_reason,
                    kind=kind,
                    sticker_set_name=sticker_set_name,
                    sticker_name=sticker_name,
                    agent=agent,
                    **metadata,
                )
            else:
                # Other API errors (500, timeout, etc.) - treat as temporary failure
                return make_error_record(
                    unique_id,
                    MediaStatus.TEMPORARY_FAILURE,
                    f"LLM API error: {error_str[:100]}",
                    retryable=True,
                    kind=kind,
                    sticker_set_name=sticker_set_name,
                    sticker_name=sticker_name,
                    agent=agent,
                    **metadata,
                )
        except RetryableLLMError as e:
            # RetryableLLMError wraps retryable errors (timeouts, 429, 500, 502, 503, etc.)
            # These should be treated as temporary failures
            logger.warning(
                f"AIGeneratingMediaSource: retryable LLM error for {unique_id}: {e}"
            )

            # Clean up temporary TGS and video files if conversion succeeded
            if is_converted_tgs:
                if video_file_path and video_file_path.exists():
                    video_file_path.unlink()
                if "tgs_path" in locals() and tgs_path and tgs_path.exists():
                    tgs_path.unlink()

            # Transient failure - return error record for AIChainMediaSource to handle
            return make_error_record(
                unique_id,
                MediaStatus.TEMPORARY_FAILURE,
                f"LLM error (retryable): {str(e)[:100]}",
                retryable=True,
                kind=kind,
                sticker_set_name=sticker_set_name,
                sticker_name=sticker_name,
                agent=agent,
                **metadata,
            )
        except Exception as e:
            logger.exception(
                f"AIGeneratingMediaSource: LLM failed for {unique_id}: {e}"
            )

            # Clean up temporary TGS and video files if conversion succeeded
            if is_converted_tgs:
                if video_file_path and video_file_path.exists():
                    video_file_path.unlink()
                if "tgs_path" in locals() and tgs_path and tgs_path.exists():
                    tgs_path.unlink()

            # Permanent failure - return error record for AIChainMediaSource to handle
            return make_error_record(
                unique_id,
                MediaStatus.PERMANENT_FAILURE,
                f"description failed: {str(e)[:100]}",
                kind=kind,
                sticker_set_name=sticker_set_name,
                sticker_name=sticker_name,
                agent=agent,
                **metadata,
            )

        llm_ms = (time.perf_counter() - t1) * 1000

        # If LLM returned empty or invalid description, use make_error_record
        # to ensure stickers get fallback descriptions (consistent with exception paths)
        if not desc:
            # Clean up temporary TGS and video files if conversion succeeded
            if is_converted_tgs:
                if video_file_path and video_file_path.exists():
                    video_file_path.unlink()
                if "tgs_path" in locals() and tgs_path and tgs_path.exists():
                    tgs_path.unlink()

            return make_error_record(
                unique_id,
                MediaStatus.PERMANENT_FAILURE,
                "LLM returned empty or invalid description",
                kind=kind,
                sticker_set_name=sticker_set_name,
                sticker_name=sticker_name,
                agent=agent,
                **metadata,
            )

        # Return record for AIChainMediaSource to handle caching
        # For converted TGS files, ensure mime_type reflects the original TGS file (on disk),
        # not the temporary video/mp4 used for AI processing
        record_metadata = metadata.copy()
        if is_converted_tgs and "original_mime_type" in record_metadata:
            # Restore original TGS mime_type for the saved record
            record_metadata["mime_type"] = record_metadata["original_mime_type"]
        
        record = {
            "unique_id": unique_id,
            "kind": kind,
            "sticker_set_name": sticker_set_name,
            "sticker_name": sticker_name,
            "description": desc,
            "failure_reason": None,
            "status": MediaStatus.GENERATED.value,
            "ts": clock.now(UTC).isoformat(),
            **record_metadata,
        }
        # Add agent_telegram_id if available and not already in metadata
        if agent is not None and "agent_telegram_id" not in record:
            agent_telegram_id = getattr(agent, "agent_id", None)
            if agent_telegram_id is not None:
                record["agent_telegram_id"] = agent_telegram_id

        total_ms = (time.perf_counter() - t0) * 1000
        logger.debug(
            f"AIGeneratingMediaSource: SUCCESS {unique_id} bytes={len(data)} dl={dl_ms:.0f}ms llm={llm_ms:.0f}ms total={total_ms:.0f}ms"
        )

        # Clean up temporary TGS and video files if conversion succeeded
        if is_converted_tgs:
            if video_file_path and video_file_path.exists():
                video_file_path.unlink()
            if "tgs_path" in locals() and tgs_path and tgs_path.exists():
                tgs_path.unlink()

        return record

