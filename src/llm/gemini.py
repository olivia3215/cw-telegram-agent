# llm/gemini.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import asyncio
import base64
import json
import logging
import os
from collections.abc import Iterable
from typing import Any

import httpx
from google import genai
from google.genai.types import (
    FinishReason,
    GenerateContentConfig,
    HarmBlockThreshold,
    HarmCategory,
)

from config import GOOGLE_GEMINI_API_KEY
from media.mime_utils import detect_mime_type_from_bytes, is_tgs_mime_type

from .base import LLM, ChatMsg, MsgPart

logger = logging.getLogger(__name__)

# Debug logging flag
GEMINI_DEBUG_LOGGING: bool = os.environ.get("GEMINI_DEBUG_LOGGING", "").lower() in (
    "true",
    "1",
    "yes",
    "on",
)


class GeminiLLM(LLM):
    prompt_name = "Gemini"

    def __init__(
        self,
        model: str = "gemini-2.5-flash-preview-09-2025",
        api_key: str | None = None,
    ):
        self.model_name = model
        self.api_key = api_key or GOOGLE_GEMINI_API_KEY
        if not self.api_key:
            raise ValueError(
                "Missing Gemini API key. Set GOOGLE_GEMINI_API_KEY or pass it explicitly."
            )
        self.client = genai.Client(api_key=self.api_key)
        self.history_size = 20

        # Configure safety settings to disable content filtering
        # Note: Only disable HARM_CATEGORY_SEXUALLY_EXPLICIT as other categories may cause issues
        self.safety_settings = [
            {
                "category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                "threshold": HarmBlockThreshold.BLOCK_NONE,
            },
            {
                "category": HarmCategory.HARM_CATEGORY_HARASSMENT,
                "threshold": HarmBlockThreshold.BLOCK_NONE,
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

    IMAGE_DESCRIPTION_PROMPT = (
        "You are given a single image. Describe the scene in rich detail so a reader "
        "can understand it without seeing the image. Include salient objects, colors, "
        "relations, actions, and setting. Output only the description."
    )

    VIDEO_DESCRIPTION_PROMPT = (
        "You are given a short video. Describe what happens in the video in rich detail "
        "so a reader can understand it without seeing the video. Include salient objects, "
        "colors, actions, movement, and what the video shows. Output only the description."
    )

    AUDIO_DESCRIPTION_PROMPT = (
        "You are given an audio file. Describe what you hear in rich detail "
        "so a reader can understand the audio content without hearing it. Include "
        "a complete speech transcription, music, sounds, and any other audio elements. "
        "Estimate the speaker's age, gender, and accent if they are human. "
        "Output only the description."
    )

    def is_mime_type_supported_by_llm(self, mime_type: str) -> bool:
        """
        Check if a MIME type is supported by the LLM for media description.
        Returns True for static image formats and video formats that Gemini can process.
        """
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
        return mime_type.lower() in supported_types

    def is_audio_mime_type_supported(self, mime_type: str) -> bool:
        """
        Check if a MIME type is supported by the LLM for audio description.
        Returns True for audio formats that Gemini can process.
        """
        supported_types = {
            # Audio formats
            "audio/ogg",  # Telegram voice messages
            "audio/mpeg",  # MP3
            "audio/wav",  # WAV
            "audio/mp4",  # M4A
            "audio/webm",  # WebM audio
            "audio/flac",  # FLAC
        }
        return mime_type.lower() in supported_types

    async def describe_image(
        self,
        image_bytes: bytes,
        mime_type: str | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given image.
        Uses Gemini via REST with this instance's api key.
        Raises on failures so the scheduler's retry policy can handle it.
        """
        if not self.api_key:
            raise ValueError("Missing Gemini API key")

        # Use centralized MIME type detection if not provided
        if not mime_type:
            mime_type = detect_mime_type_from_bytes(image_bytes)

        # Special handling for TGS files (gzip-compressed Lottie animations)
        # Gemini doesn't support application/gzip for image/video analysis
        if is_tgs_mime_type(mime_type):
            raise ValueError(
                f"TGS animated stickers (MIME type {mime_type}) are not supported for AI image analysis. "
                f"Use sticker metadata for description instead."
            )

        # Check if this MIME type is supported by the LLM
        if not self.is_mime_type_supported_by_llm(mime_type):
            raise ValueError(
                f"MIME type {mime_type} is not supported by Gemini for image description"
            )

        # Use gemini-2.0-flash for image descriptions
        model = "gemini-2.0-flash"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"

        # Use cached REST API format safety settings
        safety_settings_rest = self._safety_settings_rest_cache

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": self.IMAGE_DESCRIPTION_PROMPT},
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
            raise RuntimeError(
                f"Gemini HTTP {e.response.status_code}: {e.response.text}"
            ) from e
        except Exception as e:
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
            return parts[0]["text"].strip()
        except Exception as e:
            raise RuntimeError(f"Gemini parse error: {e}") from e

    async def describe_video(
        self,
        video_bytes: bytes,
        mime_type: str | None = None,
        duration: int | None = None,
        timeout_s: float | None = None,
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
            ValueError: If video is too long (>10 seconds) or MIME type unsupported
            RuntimeError: For API failures
        """
        if not self.api_key:
            raise ValueError("Missing Gemini API key")

        # Check video duration - reject videos longer than 10 seconds
        if duration is not None and duration > 10:
            raise ValueError(
                f"Video is too long to analyze (duration: {duration}s, max: 10s)"
            )

        # Use centralized MIME type detection if not provided
        if not mime_type:
            mime_type = detect_mime_type_from_bytes(video_bytes)

        # Special handling for TGS files (gzip-compressed Lottie animations)
        # Gemini doesn't support application/gzip for video analysis
        if is_tgs_mime_type(mime_type):
            raise ValueError(
                f"TGS animated stickers (MIME type {mime_type}) are not supported for AI video analysis. "
                f"Use sticker metadata for description instead."
            )

        # Check if this MIME type is supported by the LLM
        if not self.is_mime_type_supported_by_llm(mime_type):
            raise ValueError(
                f"MIME type {mime_type} is not supported by Gemini for video description"
            )

        # Use gemini-2.0-flash for video descriptions
        model = "gemini-2.0-flash"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"

        # Use cached REST API format safety settings
        safety_settings_rest = self._safety_settings_rest_cache

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": self.VIDEO_DESCRIPTION_PROMPT},
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
            raise RuntimeError(
                f"Gemini HTTP {e.response.status_code}: {e.response.text}"
            ) from e
        except Exception as e:
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
            return parts[0]["text"].strip()
        except Exception as e:
            raise RuntimeError(f"Gemini parse error: {e}") from e

    async def describe_audio(
        self,
        audio_bytes: bytes,
        mime_type: str | None = None,
        duration: int | None = None,
        timeout_s: float | None = None,
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
            raise ValueError("Missing Gemini API key")

        # Check audio duration - reject audio longer than 1 minute
        if duration is not None and duration > 60:
            raise ValueError(
                f"Audio is too long to analyze (duration: {duration}s, max: 60s)"
            )

        # Use centralized MIME type detection if not provided
        if not mime_type:
            mime_type = detect_mime_type_from_bytes(audio_bytes)

        # Check if this MIME type is supported by the LLM
        if not self.is_audio_mime_type_supported(mime_type):
            raise ValueError(
                f"MIME type {mime_type} is not supported by Gemini for audio description"
            )

        # Use gemini-2.0-flash for audio descriptions
        model = "gemini-2.0-flash"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"

        # Use cached REST API format safety settings
        safety_settings_rest = self._safety_settings_rest_cache

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": self.AUDIO_DESCRIPTION_PROMPT},
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
            raise RuntimeError(
                f"Gemini HTTP {e.response.status_code}: {e.response.text}"
            ) from e
        except Exception as e:
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
            return parts[0]["text"].strip()
        except Exception as e:
            raise RuntimeError(f"Gemini parse error: {e}") from e

    async def _generate_with_contents(
        self,
        *,
        contents: list[dict[str, object]],
        model: str | None = None,
        timeout_s: float | None = None,
        system_instruction: str | None = None,
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
            config = GenerateContentConfig(
                system_instruction=system_instruction,
                safety_settings=self.safety_settings,
            )

            for _ in range(3):  # try three times.
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=model_name,
                    contents=contents_norm,
                    config=config,
                )

                # Optional comprehensive logging for debugging
                if GEMINI_DEBUG_LOGGING:
                    logger.info("=== GEMINI_DEBUG_LOGGING: COMPLETE RESPONSE ===")
                    if response is not None:
                        logger.info(f"Response object type: {type(response)}")
                        if hasattr(response, "text") and isinstance(response.text, str):
                            logger.info(f"Response text: {response.text}")
                        if hasattr(response, "candidates") and response.candidates:
                            logger.info(
                                f"Number of candidates: {len(response.candidates)}"
                            )
                            for i, candidate in enumerate(response.candidates):
                                logger.info(f"  Candidate {i+1}:")
                                if hasattr(candidate, "finish_reason"):
                                    logger.info(
                                        f"    Finish reason: {candidate.finish_reason}"
                                    )
                                if hasattr(candidate, "safety_ratings"):
                                    logger.info(
                                        f"    Safety ratings: {candidate.safety_ratings}"
                                    )
                    logger.info("=== END GEMINI_DEBUG_LOGGING: RESPONSE ===")

                # Extract the first candidate's text safely
                text = ""
                if response is not None:
                    if hasattr(response, "text") and isinstance(response.text, str):
                        text = response.text
                    elif hasattr(response, "candidates") and response.candidates:
                        cand = response.candidates[0]
                        if cand.finish_reason == FinishReason.PROHIBITED_CONTENT:
                            logger.warning(
                                "Gemini returned prohibited content, trying again"
                            )
                            continue  # try again
                        t = getattr(cand, "text", None)
                        if isinstance(t, str):
                            text = t or ""
                        else:
                            content = getattr(cand, "content", None)
                            if content and getattr(content, "parts", None):
                                first_part = content.parts[0]
                                if (
                                    isinstance(first_part, dict)
                                    and "text" in first_part
                                ):
                                    text = str(first_part["text"] or "")

                break

            return text or ""
        except Exception as e:
            logger.error("SDK exception: %s", e)
            # Return the exception so we can determine if it's retryable
            raise e

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
        if who and sid:
            header_bits.append(f'sender="{who}" sender_id={sid}')
        elif who or sid:
            header_bits.append(f"sender_id={who or sid}")
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
        any_user_messages = False
        any_agent_messages = False
        for m in history:
            is_agent = bool(m.get("is_agent"))
            any_user_messages = any_user_messages or not is_agent
            any_agent_messages = any_agent_messages or is_agent
            role = "assistant" if is_agent else "user"
            parts = self._normalize_parts_for_message(
                m,
                is_agent=is_agent,
            )
            if parts:
                contents.append({"role": role, "parts": parts})

        # Ensure we have at least one user turn for Gemini's requirements
        if not any_user_messages:
            if any_agent_messages:
                special_user_message = "[special] The user has not responded yet."
            else:
                special_user_message = "[special] This is the beginning of a conversation. Please respond with your first message."
            contents.append(
                {"role": "user", "parts": [self._mk_text_part(special_user_message)]}
            )

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
        )
