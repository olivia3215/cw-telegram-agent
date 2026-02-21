# src/llm/gemini.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import asyncio
import base64
import copy
import json
import logging
import os
import pprint
from collections.abc import Iterable
from typing import Any

import httpx  # pyright: ignore[reportMissingImports]
from google import genai  # pyright: ignore[reportMissingImports]
from google.genai.types import (  # pyright: ignore[reportMissingImports]
    FinishReason,
    GenerateContentConfig,
    HarmBlockThreshold,
    HarmCategory,
)

import config
from config import GOOGLE_GEMINI_API_KEY, GEMINI_MODEL
from media.mime_utils import (
    detect_mime_type_from_bytes,
    is_tgs_mime_type,
    normalize_mime_type,
)

from .base import LLM, ChatMsg, MsgPart
from .exceptions import RetryableLLMError
from .task_schema import get_task_response_schema_dict

logger = logging.getLogger(__name__)

# SDK logs a WARNING every time it builds concatenated text from a response that
# contains non-text parts (e.g. thought_signature). We touch the response several
# times (extract text, usage_metadata, optional pprint), so we get repeated noise.
# Suppress WARNING for that logger so we only see ERROR+ from the SDK.
logging.getLogger("google_genai.types").setLevel(logging.ERROR)

# Debug logging flag
GEMINI_DEBUG_LOGGING: bool = os.environ.get("GEMINI_DEBUG_LOGGING", "").lower() in (
    "true",
    "1",
    "yes",
    "on",
)


_TASK_RESPONSE_SCHEMA_DICT = get_task_response_schema_dict()


# Use shared utility function
from .utils import format_string_for_logging as _format_string_for_logging


# Import shared utility function
from llm.base import extract_gemini_response_text as _extract_response_text


class GeminiLLM(LLM):
    prompt_name = "Instructions"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.api_key = api_key or GOOGLE_GEMINI_API_KEY
        if not self.api_key:
            raise ValueError(
                "Missing Gemini API key. Set GOOGLE_GEMINI_API_KEY or pass it explicitly."
            )
        # Use provided model, or GEMINI_MODEL env var, or raise error
        if model:
            self.model_name = model
        elif GEMINI_MODEL:
            self.model_name = GEMINI_MODEL
        else:
            raise ValueError(
                "Missing model specification. Either pass 'model' parameter or set GEMINI_MODEL environment variable."
            )
        self.client = genai.Client(api_key=self.api_key)
        self.history_size = 100

        # Configure safety settings to disable content filtering
        # Note: Only disable HARM_CATEGORY_SEXUALLY_EXPLICIT as other categories may cause issues
        self.safety_settings = [
            # {
            #     "category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            #     "threshold": HarmBlockThreshold.BLOCK_NONE,
            # },
            {
                "category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                "threshold": HarmBlockThreshold.OFF,
            },
            {
                "category": HarmCategory.HARM_CATEGORY_HARASSMENT,
                "threshold": HarmBlockThreshold.OFF,
            },
            # Other categories commented out as they may cause problems:
            # - HARM_CATEGORY_CIVIC_INTEGRITY
            # - HARM_CATEGORY_DANGEROUS_CONTENT
            # - HARM_CATEGORY_HATE_SPEECH
            # These categories are NOT supported by the stable model:
            # - HARM_CATEGORY_IMAGE_* (all image-related categories)
            # - HARM_CATEGORY_UNSPECIFIED
        ]

        # Cache the REST API format to avoid recomputing it
        self._safety_settings_rest_cache = self._safety_settings_to_rest_format()

    def _log_usage_from_rest_response(
        self,
        obj: dict,
        agent: Any | None,
        model_name: str,
        operation: str,
        channel_telegram_id: int | None = None,
    ) -> None:
        """
        Log LLM usage from a REST API response.
        
        Args:
            obj: The parsed JSON response object
            agent: Optional agent object for logging context
            model_name: Model name for logging
            operation: Operation type (e.g., "describe_image", "describe_video")
        """
        try:
            # Extract usage metadata from REST API response
            usage = obj.get("usageMetadata", {})
            input_tokens = usage.get("promptTokenCount", 0)
            output_tokens = usage.get("candidatesTokenCount", 0)
            # Thinking tokens are billed as output tokens per Google's pricing
            thinking_tokens = usage.get("thoughtsTokenCount", 0)
            total_output_tokens = output_tokens + thinking_tokens
            
            if input_tokens or total_output_tokens:
                from .usage_logging import log_llm_usage
                log_llm_usage(
                    agent=agent,
                    model_name=model_name,
                    input_tokens=input_tokens,
                    output_tokens=total_output_tokens,
                    operation=operation,
                    channel_telegram_id=channel_telegram_id,
                )
        except Exception as e:
            # Don't fail the request if usage logging fails
            logger.warning(f"Failed to log LLM usage: {e}")

    def _log_usage_from_sdk_response(
        self,
        response: Any,
        agent: Any | None,
        model_name: str,
        operation: str | None = None,
        channel_telegram_id: int | None = None,
    ) -> None:
        """
        Log LLM usage from an SDK response object.
        
        Args:
            response: The SDK response object
            agent: Optional agent object for logging context
            model_name: Model name for logging
            operation: Optional operation type (e.g., "query_structured")
        """
        if response is None:
            return
            
        try:
            # Gemini responses have usage_metadata attribute
            if hasattr(response, "usage_metadata"):
                usage = response.usage_metadata
                input_tokens = getattr(usage, "prompt_token_count", 0)
                output_tokens = getattr(usage, "candidates_token_count", 0)
                # Thinking tokens are billed as output tokens per Google's pricing
                thinking_tokens = getattr(usage, "thoughts_token_count", 0)
                total_output_tokens = output_tokens + thinking_tokens
                
                from .usage_logging import log_llm_usage
                log_llm_usage(
                    agent=agent,
                    model_name=model_name,
                    input_tokens=input_tokens,
                    output_tokens=total_output_tokens,
                    operation=operation,
                    channel_telegram_id=channel_telegram_id,
                )
        except Exception as e:
            # Don't fail the request if usage logging fails
            logger.warning(f"Failed to log LLM usage: {e}")

    def _safety_settings_to_rest_format(self) -> list[dict[str, str]]:
        """
        Convert client API safety settings to REST API format.
        Returns safety settings in the format expected by the REST API.
        """
        rest_settings = []
        for setting in self.safety_settings:
            category = setting["category"]
            threshold = setting["threshold"]

            # Convert category from enum to string
            if hasattr(category, "name"):
                category_str = category.name
            else:
                category_str = str(category)

            # Convert threshold from enum to string
            if hasattr(threshold, "name"):
                threshold_str = threshold.name
            else:
                threshold_str = str(threshold)

            rest_settings.append(
                {
                    "category": category_str,
                    "threshold": threshold_str,
                }
            )

        return rest_settings

    def is_mime_type_supported_by_llm(self, mime_type: str) -> bool:
        """
        Check if a MIME type is supported by the LLM for media description.
        Returns True for static image formats and video formats that Gemini can process.
        """
        mime_type = normalize_mime_type(mime_type)
        if not mime_type:
            return False

        supported_types = {
            # Images
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/gif",
            "image/webp",
            # Videos
            "video/mp4",
            "video/mpeg",
            "video/mov",
            "video/avi",
            "video/x-flv",
            "video/mpg",
            "video/webm",
            "video/wmv",
            "video/3gpp",
            "video/quicktime",
            # Telegram animated stickers
            "application/x-tgsticker",
            "application/gzip",
        }
        return mime_type in supported_types

    def is_audio_mime_type_supported(self, mime_type: str) -> bool:
        """
        Check if a MIME type is supported by the LLM for audio description.
        Returns True for audio formats that Gemini can process.
        """
        mime_type = normalize_mime_type(mime_type)
        if not mime_type:
            return False

        supported_types = {
            # Audio formats
            "audio/ogg",  # Telegram voice messages
            "audio/mpeg",  # MP3
            "audio/wav",  # WAV
            "audio/mp4",  # M4A
            "audio/webm",  # WebM audio
            "audio/flac",  # FLAC
        }
        return mime_type in supported_types

    async def describe_image(
        self,
        image_bytes: bytes,
        agent: Any | None = None,
        mime_type: str | None = None,
        timeout_s: float | None = None,
        channel_telegram_id: int | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given image.
        Uses Gemini via REST with this instance's api key.
        Raises on failures so the scheduler's retry policy can handle it.
        """
        if not self.api_key:
            error = ValueError("Missing Gemini API key")
            error.is_retryable = False
            raise error

        # Use centralized MIME type detection if not provided
        if not mime_type:
            mime_type = detect_mime_type_from_bytes(image_bytes)

        mime_type = normalize_mime_type(mime_type)

        # Special handling for TGS files (gzip-compressed Lottie animations)
        # Gemini doesn't support application/gzip for image/video analysis
        if is_tgs_mime_type(mime_type):
            error = ValueError(
                f"TGS animated stickers (MIME type {mime_type}) are not supported for AI image analysis. "
                f"Use sticker metadata for description instead."
            )
            error.is_retryable = False
            raise error

        # Check if this MIME type is supported by the LLM
        if not self.is_mime_type_supported_by_llm(mime_type):
            error = ValueError(
                f"MIME type {mime_type} is not supported by Gemini for image description"
            )
            error.is_retryable = False
            raise error

        # Assert that this instance is the correct type for media LLM (caller should select the correct LLM)
        from .media_helper import get_media_llm
        
        media_llm = get_media_llm()
        if type(media_llm) != type(self):
            raise RuntimeError(
                f"GeminiLLM.describe_image called on wrong LLM type. "
                f"Expected media_llm type {type(media_llm).__name__} to be {type(self).__name__}. "
                f"Caller should use get_media_llm() to get the correct instance."
            )
        
        # Use this instance's model and API key
        model = self.model_name
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"

        # Use cached REST API format safety settings
        safety_settings_rest = self._safety_settings_rest_cache

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": self.image_description_prompt},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(image_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "safety_settings": safety_settings_rest,
        }

        # Use provided timeout or default to 30 seconds
        timeout = timeout_s or 30.0

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url, json=payload, headers={"Content-Type": "application/json"}
                )
                response.raise_for_status()
                body = response.content
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            error_msg = f"Gemini HTTP {status_code}: {e.response.text}"
            # Check if this is a retryable HTTP status
            if status_code in (429, 500, 502, 503):
                # Retryable HTTP errors
                raise RetryableLLMError(error_msg, original_exception=e) from e
            elif status_code in (400, 401, 403, 404, 501):
                # Permanent HTTP errors - mark as non-retryable
                # 501 "Not Implemented" is permanent - server doesn't support the functionality
                runtime_error = RuntimeError(error_msg)
                runtime_error.is_retryable = False
                raise runtime_error from e
            else:
                # Other HTTP errors - use fallback logic
                raise RuntimeError(error_msg) from e
        except Exception as e:
            # Check if this is a retryable error
            from handlers.received_helpers.llm_query import is_retryable_llm_error
            
            if is_retryable_llm_error(e):
                raise RetryableLLMError(f"Gemini request failed: {e}", original_exception=e) from e
            else:
                raise RuntimeError(f"Gemini request failed: {e}") from e

        try:
            obj = json.loads(body.decode("utf-8"))
            # Typical path: candidates[0].content.parts[0].text
            candidates = obj.get("candidates") or []
            if not candidates:
                raise RuntimeError(f"Gemini returned no candidates: {obj}")
            parts = (candidates[0].get("content") or {}).get("parts") or []
            if not parts or "text" not in parts[0]:
                raise RuntimeError(f"Gemini returned no text parts: {obj}")
            
            text = parts[0]["text"].strip()
            
            # Log usage
            self._log_usage_from_rest_response(
                obj,
                agent,
                model,
                "describe_image",
                channel_telegram_id=channel_telegram_id,
            )
            
            return text
        except Exception as e:
            raise RuntimeError(f"Gemini parse error: {e}") from e

    async def describe_video(
        self,
        video_bytes: bytes,
        agent: Any | None = None,
        mime_type: str | None = None,
        duration: int | None = None,
        timeout_s: float | None = None,
        channel_telegram_id: int | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given video.
        Uses Gemini via REST with this instance's api key.
        Raises on failures so the scheduler's retry policy can handle it.

        Args:
            video_bytes: The video file bytes
            mime_type: MIME type of the video (e.g., "video/mp4")
            duration: Video duration in seconds (optional, used for validation)
            timeout_s: Request timeout in seconds

        Returns:
            Description string

        Raises:
            ValueError: If video is too long (exceeds MEDIA_VIDEO_MAX_DURATION_SECONDS) or MIME type unsupported
            RuntimeError: For API failures
        """
        if not self.api_key:
            error = ValueError("Missing Gemini API key")
            error.is_retryable = False
            raise error

        max_duration = config.MEDIA_VIDEO_MAX_DURATION_SECONDS
        if duration is not None and duration > max_duration:
            error = ValueError(
                f"Video is too long to analyze (duration: {duration}s, max: {max_duration}s)"
            )
            error.is_retryable = False
            raise error

        # Use centralized MIME type detection if not provided
        if not mime_type:
            mime_type = detect_mime_type_from_bytes(video_bytes)

        mime_type = normalize_mime_type(mime_type)

        # Special handling for TGS files (gzip-compressed Lottie animations)
        # Gemini doesn't support application/gzip for video analysis
        if is_tgs_mime_type(mime_type):
            error = ValueError(
                f"TGS animated stickers (MIME type {mime_type}) are not supported for AI video analysis. "
                f"Use sticker metadata for description instead."
            )
            error.is_retryable = False
            raise error

        # Check if this MIME type is supported by the LLM
        if not self.is_mime_type_supported_by_llm(mime_type):
            error = ValueError(
                f"MIME type {mime_type} is not supported by Gemini for video description"
            )
            error.is_retryable = False
            raise error
        
        # Check file size - Gemini has limits on inline_data size (typically 20MB)
        # Base64 encoding increases size by ~33%, so we check the original size
        MAX_VIDEO_SIZE = 20 * 1024 * 1024  # 20MB
        if len(video_bytes) > MAX_VIDEO_SIZE:
            size_mb = len(video_bytes) / (1024 * 1024)
            error = ValueError(
                f"Video file is too large ({size_mb:.1f}MB, max {MAX_VIDEO_SIZE / (1024 * 1024):.0f}MB) "
                f"for Gemini inline_data API"
            )
            error.is_retryable = False
            raise error
        
        # Validate video file format - check if it's actually a valid MP4/WebM/etc.
        # MP4 files should start with ftyp box at offset 4
        # WebM files should start with EBML header
        if mime_type == "video/mp4":
            if len(video_bytes) < 8 or video_bytes[4:8] != b"ftyp":
                error = ValueError(
                    f"Video file does not appear to be a valid MP4 (missing ftyp box). "
                    f"File may be corrupted or in wrong format."
                )
                error.is_retryable = False
                raise error
        elif mime_type == "video/webm":
            if len(video_bytes) < 4 or video_bytes[:4] != b"\x1a\x45\xdf\xa3":
                error = ValueError(
                    f"Video file does not appear to be a valid WebM (missing EBML header). "
                    f"File may be corrupted or in wrong format."
                )
                error.is_retryable = False
                raise error

        # Assert that this instance is the correct type for media LLM (caller should select the correct LLM)
        from .media_helper import get_media_llm
        
        media_llm = get_media_llm()
        if type(media_llm) != type(self):
            raise RuntimeError(
                f"GeminiLLM.describe_video called on wrong LLM type. "
                f"Expected media_llm type {type(media_llm).__name__} to be {type(self).__name__}. "
                f"Caller should use get_media_llm() to get the correct instance."
            )
        
        # Use this instance's model and API key
        model = self.model_name
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"

        # Use cached REST API format safety settings
        safety_settings_rest = self._safety_settings_rest_cache

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": self.video_description_prompt},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(video_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "safety_settings": safety_settings_rest,
        }

        # Use provided timeout or default to 60 seconds (videos take longer)
        timeout = timeout_s or 60.0

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url, json=payload, headers={"Content-Type": "application/json"}
                )
                response.raise_for_status()
                body = response.content
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            error_msg = f"Gemini HTTP {status_code}: {e.response.text}"
            # Check if this is a retryable HTTP status
            if status_code in (429, 500, 502, 503):
                # Retryable HTTP errors
                raise RetryableLLMError(error_msg, original_exception=e) from e
            elif status_code in (400, 401, 403, 404, 501):
                # Permanent HTTP errors - mark as non-retryable
                # 501 "Not Implemented" is permanent - server doesn't support the functionality
                runtime_error = RuntimeError(error_msg)
                runtime_error.is_retryable = False
                raise runtime_error from e
            else:
                # Other HTTP errors - use fallback logic
                raise RuntimeError(error_msg) from e
        except Exception as e:
            # Check if this is a retryable error
            from handlers.received_helpers.llm_query import is_retryable_llm_error
            
            if is_retryable_llm_error(e):
                raise RetryableLLMError(f"Gemini request failed: {e}", original_exception=e) from e
            else:
                raise RuntimeError(f"Gemini request failed: {e}") from e

        try:
            obj = json.loads(body.decode("utf-8"))
            # Typical path: candidates[0].content.parts[0].text
            candidates = obj.get("candidates") or []
            if not candidates:
                raise RuntimeError(f"Gemini returned no candidates: {obj}")
            parts = (candidates[0].get("content") or {}).get("parts") or []
            if not parts or "text" not in parts[0]:
                raise RuntimeError(f"Gemini returned no text parts: {obj}")
            
            text = parts[0]["text"].strip()
            
            # Log usage
            self._log_usage_from_rest_response(
                obj,
                agent,
                model,
                "describe_video",
                channel_telegram_id=channel_telegram_id,
            )
            
            return text
        except Exception as e:
            raise RuntimeError(f"Gemini parse error: {e}") from e

    async def describe_audio(
        self,
        audio_bytes: bytes,
        agent: Any | None = None,
        mime_type: str | None = None,
        duration: int | None = None,
        timeout_s: float | None = None,
        channel_telegram_id: int | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given audio.
        Uses Gemini via REST with this instance's api key.
        Raises on failures so the scheduler's retry policy can handle it.

        Args:
            audio_bytes: The audio file bytes
            mime_type: MIME type of the audio (e.g., "audio/ogg", "audio/mpeg")
            duration: Audio duration in seconds (optional, used for validation)
            timeout_s: Request timeout in seconds

        Returns:
            Description string

        Raises:
            ValueError: If audio is too long (>1 minute) or MIME type unsupported
            RuntimeError: For API failures
        """
        if not self.api_key:
            error = ValueError("Missing Gemini API key")
            error.is_retryable = False
            raise error

        # Check audio duration - reject audio longer than 5 minutes.
        # As of 2025-11-09, Gemini bills audio description at ~$0.001344 per minute,
        # so extending the ceiling to 5 minutes keeps the cost at ~$0.00672.
        if duration is not None and duration > 300:
            error = ValueError(
                f"Audio is too long to analyze (duration: {duration}s, max: 300s)"
            )
            error.is_retryable = False
            raise error

        # Use centralized MIME type detection if not provided
        if not mime_type:
            mime_type = detect_mime_type_from_bytes(audio_bytes)

        mime_type = normalize_mime_type(mime_type)

        # Check if this MIME type is supported by the LLM
        if not self.is_audio_mime_type_supported(mime_type):
            error = ValueError(
                f"MIME type {mime_type} is not supported by Gemini for audio description"
            )
            error.is_retryable = False
            raise error

        # Assert that this instance is the correct type for media LLM (caller should select the correct LLM)
        from .media_helper import get_media_llm
        
        media_llm = get_media_llm()
        if type(media_llm) != type(self):
            raise RuntimeError(
                f"GeminiLLM.describe_audio called on wrong LLM type. "
                f"Expected media_llm type {type(media_llm).__name__} to be {type(self).__name__}. "
                f"Caller should use get_media_llm() to get the correct instance."
            )
        
        # Use this instance's model and API key
        model = self.model_name
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"

        # Use cached REST API format safety settings
        safety_settings_rest = self._safety_settings_rest_cache

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": self.audio_description_prompt},
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": base64.b64encode(audio_bytes).decode("ascii"),
                            }
                        },
                    ],
                }
            ],
            "safety_settings": safety_settings_rest,
        }

        # Use provided timeout or default to 60 seconds (audio analysis takes longer)
        timeout = timeout_s or 60.0

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url, json=payload, headers={"Content-Type": "application/json"}
                )
                response.raise_for_status()
                body = response.content
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            error_msg = f"Gemini HTTP {status_code}: {e.response.text}"
            # Check if this is a retryable HTTP status
            if status_code in (429, 500, 502, 503):
                # Retryable HTTP errors
                raise RetryableLLMError(error_msg, original_exception=e) from e
            elif status_code in (400, 401, 403, 404, 501):
                # Permanent HTTP errors - mark as non-retryable
                # 501 "Not Implemented" is permanent - server doesn't support the functionality
                runtime_error = RuntimeError(error_msg)
                runtime_error.is_retryable = False
                raise runtime_error from e
            else:
                # Other HTTP errors - use fallback logic
                raise RuntimeError(error_msg) from e
        except Exception as e:
            # Check if this is a retryable error
            from handlers.received_helpers.llm_query import is_retryable_llm_error
            
            if is_retryable_llm_error(e):
                raise RetryableLLMError(f"Gemini request failed: {e}", original_exception=e) from e
            else:
                raise RuntimeError(f"Gemini request failed: {e}") from e

        try:
            obj = json.loads(body.decode("utf-8"))
            # Typical path: candidates[0].content.parts[0].text
            candidates = obj.get("candidates") or []
            if not candidates:
                raise RuntimeError(f"Gemini returned no candidates: {obj}")
            parts = (candidates[0].get("content") or {}).get("parts") or []
            if not parts or "text" not in parts[0]:
                raise RuntimeError(f"Gemini returned no text parts: {obj}")
            
            text = parts[0]["text"].strip()
            
            # Log usage
            self._log_usage_from_rest_response(
                obj,
                agent,
                model,
                "describe_audio",
                channel_telegram_id=channel_telegram_id,
            )
            
            return text
        except Exception as e:
            raise RuntimeError(f"Gemini parse error: {e}") from e

    async def _generate_with_contents(
        self,
        *,
        contents: list[dict[str, object]],
        model: str | None = None,
        timeout_s: float | None = None,
        system_instruction: str | None = None,
        allowed_task_types: set[str] | None = None,
        agent: Any | None = None,
        operation: str | None = None,
        channel_telegram_id: int | None = None,
    ) -> str:
        """
        Thin wrapper around the Gemini client for role-structured 'contents'.
        Sends ONLY user/assistant turns in 'contents'. If provided, 'system_instruction'
        is passed via the model config path (never mixed into message contents).
        Returns the model's text ('' on no text). No internal retries.
        """
        try:
            client = getattr(self, "client", None)
            if client is None:
                raise RuntimeError("Gemini client not initialized")

            # Normalize roles for Gemini: assistant -> model; only "user" and "model" allowed.
            try:
                contents_norm: list[dict[str, object]] = []
                for turn in contents:
                    role = turn.get("role")
                    if role == "assistant":
                        mapped_role = "model"
                    elif role == "user":
                        mapped_role = "user"
                    else:
                        # Be conservative: anything unexpected becomes "user"
                        mapped_role = "user"
                    parts = turn.get("parts") or []
                    contents_norm.append({"role": mapped_role, "parts": parts})
            except Exception:
                contents_norm = contents

            # Optional comprehensive logging for debugging
            if GEMINI_DEBUG_LOGGING:
                logger.info("=== GEMINI_DEBUG_LOGGING: COMPLETE PROMPT ===")
                logger.info(f"System Instruction: {system_instruction}")
                logger.info(f"Contents ({len(contents_norm)} turns):")
                for i, turn in enumerate(contents_norm):
                    role = turn.get("role", "unknown")
                    parts = turn.get("parts", [])
                    logger.info(f"  Turn {i+1} ({role}):")
                    for j, part in enumerate(parts):
                        if isinstance(part, dict) and "text" in part:
                            text = part["text"]
                            # Replace newlines with \n for better log readability
                            text = text.replace("\n", "\\n")
                            # Truncate very long text for readability (5000 chars for retrieval debugging)
                            if len(text) > 5000:
                                text = text[:5000] + "... [truncated]"
                            logger.info(f"    Part {j+1}: {text}")
                        else:
                            logger.info(f"    Part {j+1}: {part}")
                logger.info("=== END GEMINI_DEBUG_LOGGING: PROMPT ===")

            # Use the new client.models.generate_content API
            model_name = model or self.model_name
            from .task_schema import get_task_response_schema_dict
            schema_dict = get_task_response_schema_dict(allowed_task_types=allowed_task_types)
            config = GenerateContentConfig(
                system_instruction=system_instruction,
                safety_settings=self.safety_settings,
                response_mime_type="application/json",
                response_json_schema=copy.deepcopy(schema_dict),
            )

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model_name,
                contents=contents_norm,
                config=config,
            )

            # Check for prohibited content before extraction
            # Check both prompt_feedback.block_reason and candidate.finish_reason
            if response is not None:
                # Check prompt_feedback for blocked content (happens when prompt itself is blocked)
                if hasattr(response, "prompt_feedback") and response.prompt_feedback:
                    if hasattr(response.prompt_feedback, "block_reason"):
                        block_reason = response.prompt_feedback.block_reason
                        if block_reason and str(block_reason) != "BLOCK_REASON_UNSPECIFIED":
                            logger.warning(
                                f"Gemini blocked prompt due to {block_reason} - treating as retryable failure"
                            )
                            raise RetryableLLMError(
                                f"Temporary error: prompt blocked ({block_reason}) - will retry"
                            )
                
                # Check candidate finish_reason for blocked content (happens when response is blocked)
                if hasattr(response, "candidates") and response.candidates:
                    cand = response.candidates[0]
                    if cand.finish_reason == FinishReason.PROHIBITED_CONTENT:
                        logger.warning(
                            "Gemini returned prohibited content - treating as retryable failure"
                        )
                        raise RetryableLLMError(
                            "Temporary error: prohibited content - will retry"
                        )
            
            # Extract the first candidate's text safely using the helper
            text = _extract_response_text(response)
            
            # Log usage
            self._log_usage_from_sdk_response(
                response,
                agent,
                model_name,
                operation,
                channel_telegram_id=channel_telegram_id,
            )

            # Optional comprehensive logging for debugging
            if GEMINI_DEBUG_LOGGING:
                logger.info("=== GEMINI_DEBUG_LOGGING: COMPLETE RESPONSE ===")
                if response is not None:
                    # Use pprint for readable output of complex response objects
                    logger.info(f"Response object:\n{pprint.pformat(response, width=120, compact=False)}")
                logger.info("=== END GEMINI_DEBUG_LOGGING: RESPONSE ===")

            if text.startswith("⟦"):
                # Reject response that starts with a metadata placeholder
                raise RetryableLLMError("Temporary error: response starts with a metadata placeholder - will retry")

            return text or ""
        except RetryableLLMError:
            # Already wrapped, re-raise
            raise
        except Exception as e:
            logger.error("SDK exception: %s", e)
            # Check if this is a retryable error using the existing logic
            # Import here to avoid circular dependency issues
            from handlers.received_helpers.llm_query import is_retryable_llm_error
            
            if is_retryable_llm_error(e):
                # Wrap retryable errors in RetryableLLMError
                raise RetryableLLMError(str(e), original_exception=e) from e
            else:
                # Re-raise non-retryable errors as-is
                raise

    def _mk_text_part(self, text: str) -> dict[str, str]:
        """Create a Gemini text part."""
        return {"text": text}

    def _normalize_parts_for_message(
        self,
        m: ChatMsg,
        *,
        is_agent: bool,
    ) -> list[dict[str, str]]:
        """
        Produce the sequence of Gemini text parts for a single message:
          - Leading metadata header part (sender/sender_id/message_id), even in DMs.
          - Then each original message part in order (text or rendered media).
          - If a media part lacks 'rendered_text', emit a succinct placeholder so the model
            knows media was present.
        """
        parts: list[dict[str, str]] = []

        # 1) Metadata header
        header_bits: list[str] = []
        who = m.get("sender") or ""
        sid = m.get("sender_id") or ""
        username = m.get("sender_username") or ""
        if who and sid:
            header_bits.append(f'sender="{who}" sender_id={sid}')
        elif who or sid:
            header_bits.append(f"sender_id={who or sid}")
        if username:
            header_bits.append(f"username={username}")
        if m.get("msg_id"):
            header_bits.append(f'message_id={m["msg_id"]}')
        if m.get("reply_to_msg_id"):
            header_bits.append(f'reply_to_msg_id={m["reply_to_msg_id"]}')
        if m.get("ts_iso"):
            header_bits.append(f'time="{m["ts_iso"]}"')
        if header_bits:
            parts.append(self._mk_text_part(f"⟦metadata⟧ {' '.join(header_bits)}"))

        # Add reactions metadata if present
        if m.get("reactions"):
            parts.append(self._mk_text_part(f"⟦reactions⟧ {m['reactions']}"))

        # 2) Original message content in original order
        raw_parts: list[MsgPart] | None = m.get("parts")

        if raw_parts is not None and len(raw_parts) > 0:
            for p in raw_parts:
                k = (p.get("kind") or "").lower()
                if k == "text":
                    txt = (p.get("text") or "").strip()
                    if txt:
                        parts.append(self._mk_text_part(txt))
                elif k == "media":
                    rendered = (p.get("rendered_text") or "").strip()
                    if rendered:
                        parts.append(self._mk_text_part(rendered))
                    else:
                        # Fallback: brief placeholder so the LLM knows something was here.
                        mk = (p.get("media_kind") or "media").strip()
                        uid = (p.get("unique_id") or "").strip()
                        placeholder = f"⟦{mk} present" + (
                            f" uid={uid}⟧" if uid else "⟧"
                        )
                        parts.append(self._mk_text_part(placeholder))
                else:
                    # Unknown part type: surface minimally instead of dropping.
                    parts.append(self._mk_text_part(f"⟦{k or 'unknown'} part⟧"))
        else:
            # Fallback: single text
            fallback = (m.get("text") or "").strip()
            if fallback:
                parts.append(self._mk_text_part(fallback))

        return parts

    def _build_gemini_contents(
        self,
        history: Iterable[ChatMsg],
    ) -> list[dict[str, Any]]:
        """
        Construct Gemini 'contents' with roles and multi-part messages:
          - Chronological 'user'/'assistant' turns for prior messages (bounded by history_size),
            each with an ordered list of 'parts' (metadata header first, then content parts).
          - Target message is NOT appended as a separate turn; instead, a system instruction
            is added to respond to the specific message.

        Pure function: no I/O, no network, no mutation of inputs.
        """

        # --- 2) Chronological history (bounded) ---
        contents = []
        last_message = None
        for m in history:
            is_agent = bool(m.get("is_agent"))
            role = "assistant" if is_agent else "user"
            parts = self._normalize_parts_for_message(
                m,
                is_agent=is_agent,
            )
            if parts:
                contents.append({"role": role, "parts": parts})
                last_message = m  # Track the last message that had parts

        # Ensure the last message is a user turn to comply with Gemini's requirements.
        # Gemini's generate_content API requires conversations to end with a user turn.
        # If history is empty or ends with an agent turn, add a user turn.
        # Use ⟦special⟧ prefix so agent prompts know this is not actual user input.
        if last_message is None:
            # Empty history - add a user turn requesting action
            contents.append({
                "role": "user",
                "parts": [self._mk_text_part("⟦special⟧ Please respond to the instructions provided.")]
            })
        elif bool(last_message.get("is_agent")):
            # Last message was from agent - add a continuation prompt
            contents.append({
                "role": "user",
                "parts": [self._mk_text_part("⟦special⟧ The last turn was an agent turn.")]
            })

        return contents

    async def query_structured(
        self,
        *,
        system_prompt: str,
        now_iso: str,
        chat_type: str,  # "direct" | "group"
        history: Iterable[ChatMsg],
        history_size: int = 500,
        model: str | None = None,
        timeout_s: float | None = None,
        allowed_task_types: set[str] | None = None,
        agent: Any | None = None,
        channel_telegram_id: int | None = None,
    ) -> str:
        """
        Build contents using the parts-aware builder, extract a system instruction (if present),
        and call the Gemini model with *only* user/assistant turns. The system instruction is
        provided via the model config path (preferred) and never mixed into message contents.
        """
        contents_for_call = self._build_gemini_contents(history)

        total_turns = len(contents_for_call)
        hist_turns = total_turns
        logger.debug(
            "gemini.contents (no system in contents): turns=%s (history=%s, target=%s) has_sys=%s",
            total_turns,
            hist_turns,
            bool(system_prompt),
        )

        return await self._generate_with_contents(
            contents=contents_for_call,
            model=model,
            timeout_s=timeout_s,
            system_instruction=system_prompt,
            allowed_task_types=allowed_task_types,
            agent=agent,
            operation="query_structured",
            channel_telegram_id=channel_telegram_id,
        )

    async def query_plain_text(
        self,
        *,
        system_prompt: str,
        model: str | None = None,
        timeout_s: float | None = None,
        agent: Any | None = None,
        channel_telegram_id: int | None = None,
    ) -> str:
        """Query Gemini for plain text without JSON schema constraints."""
        client = getattr(self, "client", None)
        if client is None:
            raise RuntimeError("Gemini client not initialized")

        model_name = model or self.model_name
        config = GenerateContentConfig(
            system_instruction=system_prompt,
            safety_settings=self.safety_settings,
        )

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model_name,
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"text": "⟦special⟧ Please respond to the instructions provided."}
                    ],
                }
            ],
            config=config,
        )

        text = _extract_response_text(response).strip()
        self._log_usage_from_sdk_response(
            response,
            agent,
            model_name,
            "query_plain_text",
            channel_telegram_id=channel_telegram_id,
        )
        return text or ""

    async def query_with_json_schema(
        self,
        *,
        system_prompt: str,
        json_schema: dict,
        model: str | None = None,
        timeout_s: float | None = None,
        agent: Any | None = None,
        channel_telegram_id: int | None = None,
    ) -> str:
        """
        Query Gemini with a JSON schema constraint on the response.
        
        Args:
            system_prompt: The system prompt/instruction to send to Gemini
            json_schema: JSON schema dictionary that constrains the response format
            model: Optional model name override
            timeout_s: Optional timeout in seconds for the request
        
        Returns:
            JSON string response that matches the schema
        """
        try:
            client = getattr(self, "client", None)
            if client is None:
                raise RuntimeError("Gemini client not initialized")

            model_name = model or self.model_name
            config = GenerateContentConfig(
                system_instruction=system_prompt,
                safety_settings=self.safety_settings,
                response_mime_type="application/json",
                response_json_schema=copy.deepcopy(json_schema),
            )

            # Optional comprehensive logging for debugging
            if GEMINI_DEBUG_LOGGING:
                logger.info("=== GEMINI_DEBUG_LOGGING: JSON SCHEMA QUERY ===")
                logger.info(f"Model: {model_name}")
                logger.info(f"System Prompt:\n{_format_string_for_logging(system_prompt)}")
                logger.info(f"JSON Schema:\n{json.dumps(json_schema, indent=2)}")
                logger.info("=== END GEMINI_DEBUG_LOGGING: JSON SCHEMA QUERY ===")

            # Gemini's generate_content API requires conversations to end with a user turn.
            # Since we're not passing any conversation history, add a special user turn
            # (same pattern as _build_gemini_contents when history is empty).
            contents = [{
                "role": "user",
                "parts": [{"text": "⟦special⟧ Please respond to the instructions provided."}]
            }]

            import asyncio
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=model_name,
                contents=contents,
                config=config,
            )

            # Check for prohibited content
            # Check both prompt_feedback.block_reason and candidate.finish_reason
            if response is not None:
                # Check prompt_feedback for blocked content (happens when prompt itself is blocked)
                if hasattr(response, "prompt_feedback") and response.prompt_feedback:
                    if hasattr(response.prompt_feedback, "block_reason"):
                        block_reason = response.prompt_feedback.block_reason
                        if block_reason and str(block_reason) != "BLOCK_REASON_UNSPECIFIED":
                            logger.warning(
                                f"Gemini blocked prompt for JSON schema query due to {block_reason}"
                            )
                            raise RetryableLLMError(f"Temporary error: prompt blocked ({block_reason}) - will retry")
                
                # Check candidate finish_reason for blocked content (happens when response is blocked)
                if hasattr(response, "candidates") and response.candidates:
                    cand = response.candidates[0]
                    if hasattr(cand, "finish_reason") and cand.finish_reason == FinishReason.PROHIBITED_CONTENT:
                        logger.warning("Gemini returned prohibited content for JSON schema query")
                        raise RetryableLLMError("Temporary error: prohibited content - will retry")

            # Extract text from response
            text = _extract_response_text(response)
            
            # Log usage
            self._log_usage_from_sdk_response(
                response,
                agent,
                model_name,
                "query_with_json_schema",
                channel_telegram_id=channel_telegram_id,
            )

            # Optional comprehensive logging for debugging
            if GEMINI_DEBUG_LOGGING:
                logger.info("=== GEMINI_DEBUG_LOGGING: JSON SCHEMA RESPONSE ===")
                if response is not None:
                    # Use pprint for readable output of complex response objects
                    logger.info(f"Response object:\n{pprint.pformat(response, width=120, compact=False)}")
                logger.info("=== END GEMINI_DEBUG_LOGGING: JSON SCHEMA RESPONSE ===")

            if text and not text.startswith("⟦"):
                return text

            raise RuntimeError("No valid response from Gemini")
        except RetryableLLMError:
            # Already wrapped, re-raise
            raise
        except Exception as e:
            logger.error("Gemini JSON schema query exception: %s", e)
            # Check if this is a retryable error using the existing logic
            from handlers.received_helpers.llm_query import is_retryable_llm_error
            
            if is_retryable_llm_error(e):
                # Wrap retryable errors in RetryableLLMError
                raise RetryableLLMError(str(e), original_exception=e) from e
            else:
                # Re-raise non-retryable errors as-is
                raise
