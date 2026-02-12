# src/llm/openrouter.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import base64
import json
import logging
import os
from collections.abc import Iterable
from typing import Any

from openai import AsyncOpenAI  # pyright: ignore[reportMissingImports]

from config import OPENROUTER_API_KEY
from media.mime_utils import (
    detect_mime_type_from_bytes,
    is_tgs_mime_type,
    normalize_mime_type,
)

from .base import LLM, ChatMsg, MsgPart
from .utils import format_string_for_logging as _format_string_for_logging

logger = logging.getLogger(__name__)

# Debug logging flag
OPENROUTER_DEBUG_LOGGING: bool = os.environ.get("OPENROUTER_DEBUG_LOGGING", "").lower() in (
    "true",
    "1",
    "yes",
    "on",
)


class OpenRouterLLM(LLM):
    prompt_name = "Instructions"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
    ):
        self.api_key = api_key or OPENROUTER_API_KEY
        if not self.api_key:
            raise ValueError(
                "Missing OpenRouter API key. Set OPENROUTER_API_KEY or pass it explicitly."
            )
        # Use provided model, or raise error if not provided
        if model:
            self.model_name = model
        else:
            raise ValueError(
                "Missing model specification. Must pass 'model' parameter for OpenRouter models."
            )
        # OpenRouter uses OpenAI-compatible API at https://openrouter.ai/api/v1
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url="https://openrouter.ai/api/v1",
        )
        self.history_size = 100

        # Safety settings for Gemini models (OpenRouter format uses "BLOCK_NONE" instead of "OFF")
        self.safety_settings = [
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        ]

    def _is_gemini_model(self, model_name: str) -> bool:
        """Check if model is a Gemini model."""
        model_lower = model_name.lower()
        return model_lower.startswith("google/") or "gemini" in model_lower

    def is_mime_type_supported_by_llm(self, mime_type: str) -> bool:
        """
        Check if a MIME type is supported by the LLM for media description.
        Returns True for static image formats that OpenRouter models can process.
        Note: Capabilities depend on the underlying model provider.
        """
        mime_type = normalize_mime_type(mime_type)
        if not mime_type:
            return False

        # OpenRouter models support various image formats depending on the provider
        # Most models support common image formats
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
        Returns False for now as audio capabilities vary by provider.
        """
        # TODO: Enable when specific model audio capabilities are confirmed
        return False

    async def describe_image(
        self,
        image_bytes: bytes,
        agent_name: str,
        mime_type: str | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given image.
        Uses OpenRouter via OpenAI-compatible API with this instance's api key.
        Raises on failures so the scheduler's retry policy can handle it.
        """
        # Assert that this instance is the correct type for media LLM (caller should select the correct LLM)
        from .media_helper import get_media_llm
        
        media_llm = get_media_llm()
        if type(media_llm) != type(self):
            raise RuntimeError(
                f"OpenRouterLLM.describe_image called on wrong LLM type. "
                f"Expected media_llm type {type(media_llm).__name__} to be {type(self).__name__}. "
                f"Caller should use get_media_llm() to get the correct instance."
            )
        
        # Use this instance's model and API key
        model_name = self.model_name
        api_key = self.api_key
        
        if not api_key:
            raise ValueError("Missing OpenRouter API key")

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
                f"MIME type {mime_type} is not supported by OpenRouter for image description"
            )

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
                            {"type": "text", "text": self.image_description_prompt},
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
                text = response.choices[0].message.content.strip()
            else:
                raise RuntimeError(f"OpenRouter returned no content: {response}")
            
            # Log usage
            self._log_usage_from_openai_response(response, agent_name, model_name, "describe_image")
            
            return text

        except Exception as e:
            raise RuntimeError(f"OpenRouter request failed: {e}") from e

    async def describe_video(
        self,
        video_bytes: bytes,
        mime_type: str | None = None,
        duration: int | None = None,
        timeout_s: float | None = None,
        agent_name: str | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given video.
        Currently not supported by OpenRouter - raises NotImplementedError.
        """
        raise NotImplementedError(
            "Video description is not yet supported for OpenRouter LLM. "
            "Use Gemini LLM if video description is required."
        )

    async def describe_audio(
        self,
        audio_bytes: bytes,
        mime_type: str | None = None,
        duration: int | None = None,
        timeout_s: float | None = None,
        agent_name: str | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given audio.
        Currently not supported by OpenRouter - raises NotImplementedError.
        """
        raise NotImplementedError(
            "Audio description is not yet supported for OpenRouter LLM. "
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
        history_size: int = 500,
    ) -> list[dict[str, Any]]:
        """
        Construct OpenAI-compatible messages from history.
          - System message (if provided)
          - Chronological user/assistant turns for prior messages (limited to last history_size messages),
            each with combined text parts.

        Pure function: no I/O, no network, no mutation of inputs.
        """
        messages: list[dict[str, Any]] = []

        # Add system message if provided
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Convert history to list and limit to last history_size messages
        history_list = list(history)
        if history_size > 0:
            history_list = history_list[-history_size:]

        # Add history turns
        for m in history_list:
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

        # Ensure at least one user message exists (OpenAI requirement)
        # Check if there are any user messages in the list
        has_user_message = any(msg.get("role") == "user" for msg in messages)
        
        if not has_user_message:
            # Empty history or no user messages - add a user turn requesting action
            messages.append(
                {
                    "role": "user",
                    "content": "⟦special⟧ Please respond to the instructions provided.",
                }
            )
        elif messages and messages[-1]["role"] == "assistant":
            # Last message was from assistant - add a continuation prompt
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
        allowed_task_types: set[str] | None = None,
        agent_name: str,
    ) -> str:
        """
        Build messages using the parts-aware builder and call OpenRouter with structured output.
        """
        messages = self._build_messages(history, system_prompt=system_prompt, history_size=history_size)

        total_turns = len(messages)
        logger.debug(
            "openrouter.messages: turns=%s (history=%s) has_sys=%s",
            total_turns,
            total_turns,
            bool(system_prompt),
        )

        # Optional comprehensive logging for debugging
        if OPENROUTER_DEBUG_LOGGING:
            logger.info("=== OPENROUTER_DEBUG_LOGGING: COMPLETE PROMPT ===")
            logger.info(f"Messages ({len(messages)} turns):")
            for i, msg in enumerate(messages):
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                # Show full content without truncation
                logger.info(f"  Turn {i+1} ({role}):\n{content}")
            logger.info("=== END OPENROUTER_DEBUG_LOGGING: PROMPT ===")

        model_name = model or self.model_name

        # Build response format with JSON schema if task types are specified
        response_format = None
        if allowed_task_types is not None:
            from .task_schema import get_task_response_schema_dict
            schema_dict = get_task_response_schema_dict(allowed_task_types=allowed_task_types)
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "strict": True,
                    "schema": schema_dict,
                },
            }

        try:
            # Call OpenRouter API - response should be JSON array per Instructions.md prompt
            create_kwargs = {
                "model": model_name,
                "messages": messages,
                "timeout": timeout_s or 60.0,
            }
            if response_format is not None:
                create_kwargs["response_format"] = response_format
            
            # Add safety_settings for Gemini models via extra_body
            # OpenRouter requires provider-specific parameters to be passed via extra_body
            if self._is_gemini_model(model_name):
                create_kwargs["extra_body"] = {"safety_settings": self.safety_settings}
            
            response = await self.client.chat.completions.create(**create_kwargs)

            # Optional comprehensive logging for debugging
            if OPENROUTER_DEBUG_LOGGING:
                logger.info("=== OPENROUTER_DEBUG_LOGGING: COMPLETE RESPONSE ===")
                logger.info(f"Response: {response}")
                if response.choices and response.choices[0].message.content:
                    formatted_text = _format_string_for_logging(
                        response.choices[0].message.content
                    )
                    logger.info(f"Response string:\n{formatted_text}")
                logger.info("=== END OPENROUTER_DEBUG_LOGGING: RESPONSE ===")

            # Extract text from response
            if response.choices and response.choices[0].message.content:
                text = response.choices[0].message.content.strip()
            else:
                raise RuntimeError(f"OpenRouter returned no content: {response}")
            
            # Log usage
            self._log_usage_from_openai_response(response, agent_name, model_name, "query_structured")

            if text.startswith("⟦"):
                # Reject response that starts with a metadata placeholder
                raise Exception(
                    "Temporary error: response starts with a metadata placeholder - will retry"
                )

            return text or ""

        except Exception as e:
            logger.error("OpenRouter API exception: %s", e)
            raise e

    async def query_with_json_schema(
        self,
        *,
        system_prompt: str,
        json_schema: dict,
        model: str | None = None,
        timeout_s: float | None = None,
        agent_name: str,
    ) -> str:
        """
        Query OpenRouter with a JSON schema constraint on the response.
        
        Uses OpenAI-compatible response_format with json_schema_object.
        
        Args:
            system_prompt: The system prompt/instruction to send to OpenRouter
            json_schema: JSON schema dictionary that constrains the response format
            model: Optional model name override
            timeout_s: Optional timeout in seconds for the request
        
        Returns:
            JSON string response that matches the schema
        """
        model_name = model or self.model_name

        # Optional comprehensive logging for debugging
        if OPENROUTER_DEBUG_LOGGING:
            logger.info("=== OPENROUTER_DEBUG_LOGGING: JSON SCHEMA QUERY ===")
            logger.info(f"System Prompt: {system_prompt}")
            logger.info(f"JSON Schema: {json.dumps(json_schema, indent=2)}")
            logger.info("=== END OPENROUTER_DEBUG_LOGGING: JSON SCHEMA QUERY ===")

        try:
            # OpenAI-compatible APIs require at least one user message in the messages array.
            # Add a special user message to satisfy this requirement (same pattern as OpenAI/Grok).
            create_kwargs = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "⟦special⟧ Please respond to the instructions provided."},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "response",
                        "strict": True,
                        "schema": json_schema,
                    },
                },
                "timeout": timeout_s or 60.0,
            }
            
            # Add safety_settings for Gemini models via extra_body
            # OpenRouter requires provider-specific parameters to be passed via extra_body
            if self._is_gemini_model(model_name):
                create_kwargs["extra_body"] = {"safety_settings": self.safety_settings}
            
            response = await self.client.chat.completions.create(**create_kwargs)

            # Optional comprehensive logging for debugging
            if OPENROUTER_DEBUG_LOGGING:
                logger.info("=== OPENROUTER_DEBUG_LOGGING: JSON SCHEMA RESPONSE ===")
                logger.info(f"Response: {response}")
                if response.choices and response.choices[0].message.content:
                    formatted_text = _format_string_for_logging(
                        response.choices[0].message.content
                    )
                    logger.info(f"Response string:\n{formatted_text}")
                logger.info("=== END OPENROUTER_DEBUG_LOGGING: JSON SCHEMA RESPONSE ===")

            # Extract text from response
            if response.choices and response.choices[0].message.content:
                text = response.choices[0].message.content.strip()
            else:
                raise RuntimeError(f"OpenRouter returned no content: {response}")
            
            # Log usage
            self._log_usage_from_openai_response(response, agent_name, model_name, "query_with_json_schema")

            if text.startswith("⟦"):
                # Reject response that starts with a metadata placeholder
                raise Exception(
                    "Temporary error: response starts with a metadata placeholder - will retry"
                )

            return text or ""

        except Exception as e:
            logger.error("OpenRouter JSON schema query exception: %s", e)
            raise
