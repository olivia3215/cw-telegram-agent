# llm/grok.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import asyncio
import copy
import json
import logging
import os
from collections.abc import Iterable
from typing import Any

import httpx  # pyright: ignore[reportMissingImports]
from openai import AsyncOpenAI  # pyright: ignore[reportMissingImports]

from config import GROK_API_KEY, GROK_MODEL
from media.mime_utils import (
    detect_mime_type_from_bytes,
    is_tgs_mime_type,
    normalize_mime_type,
)

from .base import LLM, ChatMsg, MsgPart
from .task_schema import get_task_response_schema_dict
from .utils import format_string_for_logging as _format_string_for_logging

logger = logging.getLogger(__name__)

# Debug logging flag
GROK_DEBUG_LOGGING: bool = os.environ.get("GROK_DEBUG_LOGGING", "").lower() in (
    "true",
    "1",
    "yes",
    "on",
)

_TASK_RESPONSE_SCHEMA_DICT = get_task_response_schema_dict()


class GrokLLM(LLM):
    prompt_name = "Instructions"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.api_key = api_key or GROK_API_KEY
        if not self.api_key:
            raise ValueError(
                "Missing Grok API key. Set GROK_API_KEY or pass it explicitly."
            )
        # Use provided model, or GROK_MODEL env var, or raise error
        if model:
            self.model_name = model
        elif GROK_MODEL:
            self.model_name = GROK_MODEL
        else:
            raise ValueError(
                "Missing model specification. Either pass 'model' parameter or set GROK_MODEL environment variable."
            )
        # Grok uses OpenAI-compatible API at https://api.x.ai/v1
        self.client = AsyncOpenAI(api_key=self.api_key, base_url="https://api.x.ai/v1")
        self.history_size = 100

    IMAGE_DESCRIPTION_PROMPT = (
        "You are given a single image. Describe the scene in rich detail so a reader "
        "can understand it without seeing the image. Include salient objects, colors, "
        "relations, actions, and setting. Output only the description."
    )

    def is_mime_type_supported_by_llm(self, mime_type: str) -> bool:
        """
        Check if a MIME type is supported by the LLM for media description.
        Returns True for static image formats that Grok can process.
        Note: Grok may have different capabilities than Gemini.
        """
        mime_type = normalize_mime_type(mime_type)
        if not mime_type:
            return False

        # Grok currently supports images via vision capabilities
        supported_types = {
            # Images
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/gif",
            "image/webp",
        }
        return mime_type in supported_types

    def is_audio_mime_type_supported(self, mime_type: str) -> bool:
        """
        Check if a MIME type is supported by the LLM for audio description.
        Returns False for now as Grok's audio capabilities may differ from Gemini's.
        """
        # TODO: Enable when Grok's audio capabilities are confirmed
        return False

    async def describe_image(
        self,
        image_bytes: bytes,
        mime_type: str | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given image.
        Uses Grok via OpenAI-compatible API with this instance's api key.
        Raises on failures so the scheduler's retry policy can handle it.
        """
        # Assert that this instance is the correct type for media LLM (caller should select the correct LLM)
        from .media_helper import get_media_llm
        
        media_llm = get_media_llm()
        if type(media_llm) != type(self):
            raise RuntimeError(
                f"GrokLLM.describe_image called on wrong LLM type. "
                f"Expected media_llm type {type(media_llm).__name__} to be {type(self).__name__}. "
                f"Caller should use get_media_llm() to get the correct instance."
            )
        
        # Use this instance's model and API key
        model_name = self.model_name
        api_key = self.api_key
        
        if not api_key:
            raise ValueError("Missing Grok API key")

        # Use centralized MIME type detection if not provided
        if not mime_type:
            mime_type = detect_mime_type_from_bytes(image_bytes)

        mime_type = normalize_mime_type(mime_type)

        # Special handling for TGS files (gzip-compressed Lottie animations)
        if is_tgs_mime_type(mime_type):
            raise ValueError(
                f"TGS animated stickers (MIME type {mime_type}) are not supported for AI image analysis. "
                f"Use sticker metadata for description instead."
            )

        # Check if this MIME type is supported by the LLM
        if not self.is_mime_type_supported_by_llm(mime_type):
            raise ValueError(
                f"MIME type {mime_type} is not supported by Grok for image description"
            )

        import base64

        # Convert image to base64
        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        image_url = f"data:{mime_type};base64,{image_base64}"

        # Use provided timeout or default to 30 seconds
        timeout = timeout_s or 30.0

        try:
            # Use this instance's client (model_name is guaranteed to be self.model_name)
            response = await self.client.chat.completions.create(
                model=model_name,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": self.IMAGE_DESCRIPTION_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": image_url},
                            },
                        ],
                    }
                ],
                timeout=timeout,
            )

            # Extract text from response
            if response.choices and response.choices[0].message.content:
                return response.choices[0].message.content.strip()
            else:
                raise RuntimeError(f"Grok returned no content: {response}")

        except Exception as e:
            raise RuntimeError(f"Grok request failed: {e}") from e

    async def describe_video(
        self,
        video_bytes: bytes,
        mime_type: str | None = None,
        duration: int | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given video.
        Currently not supported by Grok - raises NotImplementedError.
        """
        raise NotImplementedError(
            "Video description is not yet supported for Grok LLM. "
            "Use Gemini LLM if video description is required."
        )

    async def describe_audio(
        self,
        audio_bytes: bytes,
        mime_type: str | None = None,
        duration: int | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given audio.
        Currently not supported by Grok - raises NotImplementedError.
        """
        raise NotImplementedError(
            "Audio description is not yet supported for Grok LLM. "
            "Use Gemini LLM if audio description is required."
        )

    def _mk_text_part(self, text: str) -> dict[str, str]:
        """Create a text part."""
        return {"text": text}

    def _normalize_parts_for_message(
        self,
        m: ChatMsg,
        *,
        is_agent: bool,
    ) -> list[dict[str, str]]:
        """
        Produce the sequence of text parts for a single message:
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

    def _build_messages(
        self,
        history: Iterable[ChatMsg],
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Construct OpenAI-compatible messages from history.
          - System message (if provided)
          - Chronological user/assistant turns for prior messages (bounded by history_size),
            each with combined text parts.

        Pure function: no I/O, no network, no mutation of inputs.
        """
        messages: list[dict[str, Any]] = []

        # Add system message if provided
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Add history turns
        for m in history:
            is_agent = bool(m.get("is_agent"))
            role = "assistant" if is_agent else "user"
            parts = self._normalize_parts_for_message(m, is_agent=is_agent)
            if parts:
                # Combine all text parts into a single content string
                content_parts = []
                for part in parts:
                    if isinstance(part, dict) and "text" in part:
                        content_parts.append(part["text"])
                    else:
                        content_parts.append(str(part))
                content = "\n".join(content_parts)
                if content.strip():
                    messages.append({"role": role, "content": content})

        # Ensure last message is from user (OpenAI requirement)
        if messages and messages[-1]["role"] == "assistant":
            messages.append(
                {
                    "role": "user",
                    "content": "⟦special⟧ The last turn was an agent turn.",
                }
            )

        return messages

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
        Build messages using the parts-aware builder and call Grok with structured output.
        """
        messages = self._build_messages(history, system_prompt=system_prompt)

        total_turns = len(messages)
        logger.debug(
            "grok.messages: turns=%s (history=%s) has_sys=%s",
            total_turns,
            total_turns,
            bool(system_prompt),
        )

        # Optional comprehensive logging for debugging
        if GROK_DEBUG_LOGGING:
            logger.info("=== GROK_DEBUG_LOGGING: COMPLETE PROMPT ===")
            logger.info(f"Messages ({len(messages)} turns):")
            for i, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                # Replace newlines with \n for better log readability
                content_display = content.replace("\n", "\\n")
                # Truncate very long text for readability
                if len(content_display) > 5000:
                    content_display = content_display[:5000] + "... [truncated]"
                logger.info(f"  Turn {i+1} ({role}): {content_display}")
            logger.info("=== END GROK_DEBUG_LOGGING: PROMPT ===")

        model_name = model or self.model_name

        try:
            # Call Grok API - response should be JSON array per Instructions.md prompt
            response = await self.client.chat.completions.create(
                model=model_name,
                messages=messages,
                timeout=timeout_s or 60.0,
            )

            # Optional comprehensive logging for debugging
            if GROK_DEBUG_LOGGING:
                logger.info("=== GROK_DEBUG_LOGGING: COMPLETE RESPONSE ===")
                logger.info(f"Response: {response}")
                if response.choices and response.choices[0].message.content:
                    formatted_text = _format_string_for_logging(
                        response.choices[0].message.content
                    )
                    logger.info(f"Response string:\n{formatted_text}")
                logger.info("=== END GROK_DEBUG_LOGGING: RESPONSE ===")

            # Extract text from response
            if response.choices and response.choices[0].message.content:
                text = response.choices[0].message.content.strip()
            else:
                raise RuntimeError(f"Grok returned no content: {response}")

            if text.startswith("⟦"):
                # Reject response that starts with a metadata placeholder
                raise Exception(
                    "Temporary error: response starts with a metadata placeholder - will retry"
                )

            return text or ""

        except Exception as e:
            logger.error("Grok API exception: %s", e)
            raise e

