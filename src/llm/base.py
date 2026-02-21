# src/llm/base.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
import json
import pprint
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, TypedDict

# --- Type definitions for message parts and chat messages ---


class MsgTextPart(TypedDict):
    """A text part in a message."""

    kind: str  # must be "text"
    text: str  # plain text chunk


class MsgMediaPart(TypedDict, total=False):
    """A media part in a message."""

    kind: str  # must be "media"
    # Open-ended media kind (e.g., "sticker", "photo", "video", "animated_sticker", "audio", "music", ...)
    media_kind: str | None
    # Your already-rendered description string (preferred)
    rendered_text: str | None
    # Optional metadata (for trace/fallbacks)
    unique_id: str | None
    sticker_set_name: str | None
    sticker_name: str | None


MsgPart = MsgTextPart | MsgMediaPart


class ChatMsg(TypedDict, total=False):
    """
    Normalized view of a chat message for building LLM history.

    Content (one of):
      - parts: list[MsgPart]  (preferred)
      - text: str             (fallback if 'parts' missing)

    Identity / trace:
      - sender:    display name
      - sender_id: stable unique sender id (e.g., Telegram user id)
      - sender_username: username (e.g., @alice) if available
      - msg_id:    message id string (if available)
      - is_agent:  True if this message was sent by *our* agent persona
      - ts_iso:    optional ISO-8601 timestamp (trace only; not shown to model)
    """

    sender: str
    sender_id: str
    sender_username: str
    parts: list[MsgPart]
    text: str
    is_agent: bool
    msg_id: str | None
    ts_iso: str | None


# --- Utility functions ---


def extract_gemini_response_text(response: Any) -> str:
    """
    Extract text from a Gemini response object, handling various response structures.
    
    This function handles different Gemini API response formats:
    - Direct text attribute: response.text
    - Candidates with text: response.candidates[0].text
    - Candidates with content parts: response.candidates[0].content.parts[0].text
    
    Args:
        response: Gemini API response object
        
    Returns:
        Extracted text string, or empty string if no text can be extracted
    """
    if response is None:
        return ""
    
    if hasattr(response, "text") and isinstance(response.text, str):
        return response.text
    
    if hasattr(response, "candidates") and response.candidates:
        cand = response.candidates[0]
        t = getattr(cand, "text", None)
        if isinstance(t, str):
            return t or ""
        else:
            content = getattr(cand, "content", None)
            if content and getattr(content, "parts", None):
                first_part = content.parts[0]
                if isinstance(first_part, dict) and "text" in first_part:
                    return str(first_part["text"] or "")
    
    return ""


def format_openai_response_object_for_logging(response: Any) -> str:
    """
    Return a non-truncated, human-readable representation of an OpenAI-compatible response.
    Prefer JSON (model_dump) over repr(), since repr() commonly truncates long strings.
    Used by OpenAI and Grok debug logging.
    """
    try:
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            return json.dumps(dumped, indent=2, ensure_ascii=False, default=str)
    except Exception:
        pass
    try:
        return pprint.pformat(response, width=120, compact=False)
    except Exception:
        return str(response)


def format_text_as_pretty_json_if_possible(text: str) -> str:
    """If text is valid JSON, return it pretty-printed; otherwise return as-is."""
    t = (text or "").strip()
    if not t:
        return ""
    try:
        parsed = json.loads(t)
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    except Exception:
        return text


# --- Base LLM class ---


class LLM(ABC):
    """Abstract base class for all LLM implementations."""

    prompt_name: str = "Default"

    def _log_usage_from_openai_response(
        self,
        response: Any,
        agent: Any | None,
        model_name: str,
        operation: str,
        channel_telegram_id: int | None = None,
    ) -> None:
        """
        Log LLM usage from an OpenAI-compatible response.
        
        This is a shared helper for OpenAI-compatible APIs (OpenAI, Grok, OpenRouter).
        
        Args:
            response: The OpenAI-compatible response object
            agent: Optional agent object for logging context
            model_name: Model name for logging
            operation: Operation type (e.g., "describe_image", "query_structured")
        """
        if hasattr(response, 'usage') and response.usage:
            try:
                input_tokens = getattr(response.usage, 'prompt_tokens', 0)
                output_tokens = getattr(response.usage, 'completion_tokens', 0)
                
                if input_tokens or output_tokens:
                    from .usage_logging import log_llm_usage
                    log_llm_usage(
                        agent=agent,
                        model_name=model_name,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        operation=operation,
                        channel_telegram_id=channel_telegram_id,
                    )
            except Exception as e:
                # Don't fail the request if usage logging fails
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Failed to log LLM usage: {e}")

    @property
    def image_description_prompt(self) -> str:
        """Return the prompt for image description."""
        from prompt_loader import load_system_prompt
        return load_system_prompt("Instructions-Describe-Image")

    @property
    def video_description_prompt(self) -> str:
        """Return the prompt for video description."""
        from prompt_loader import load_system_prompt
        return load_system_prompt("Instructions-Describe-Video")

    @property
    def audio_description_prompt(self) -> str:
        """Return the prompt for audio description."""
        from prompt_loader import load_system_prompt
        return load_system_prompt("Instructions-Describe-Audio")

    @abstractmethod
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
        Structured query method for conversation-aware LLMs.
        Default implementation falls back to basic query method.
        
        Args:
            allowed_task_types: Optional set of task types to allow in the response schema.
                               If None, all task types are allowed.
            agent: Optional agent object for usage logging context.
        """
        ...

    @abstractmethod
    def is_mime_type_supported_by_llm(mime_type: str) -> bool:
        """
        Check if a MIME type is supported by the LLM for image description.
        Returns True for static image formats that the LLM can process.
        """
        ...

    @abstractmethod
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
        Uses the LLM to analyze the image and provide a detailed description.
        Raises on failures so the scheduler's retry policy can handle it.

        Args:
            image_bytes: The image data as bytes
            agent: Optional agent object for usage logging context
            mime_type: Optional MIME type of the image
            timeout_s: Optional timeout in seconds for the request
        """
        ...

    @abstractmethod
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
        Uses the LLM to analyze the video and provide a detailed description.
        Raises on failures so the scheduler's retry policy can handle it.

        Args:
            video_bytes: The video data as bytes
            agent: Optional agent object for usage logging context
            mime_type: Optional MIME type of the video
            duration: Video duration in seconds (optional, used for validation)
            timeout_s: Optional timeout in seconds for the request
        """
        ...

    @abstractmethod
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
        Uses the LLM to analyze the audio and provide a detailed description.
        Raises on failures so the scheduler's retry policy can handle it.

        Args:
            audio_bytes: The audio data as bytes
            agent: Optional agent object for usage logging context
            mime_type: Optional MIME type of the audio
            duration: Audio duration in seconds (optional, used for validation)
            timeout_s: Optional timeout in seconds for the request
        """
        ...

    @abstractmethod
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
        Query the LLM with a JSON schema constraint on the response.
        
        This method sends a system prompt and expects a JSON response that matches
        the provided JSON schema. The response is returned as a JSON string.
        
        Args:
            system_prompt: The system prompt/instruction to send to the LLM
            json_schema: JSON schema dictionary that constrains the response format
            model: Optional model name override
            timeout_s: Optional timeout in seconds for the request
            agent: Optional agent object for usage logging context
        
        Returns:
            JSON string response that matches the schema
        
        Raises:
            RuntimeError: If the LLM doesn't support JSON schema or returns invalid response
        """
        ...

    @abstractmethod
    async def query_plain_text(
        self,
        *,
        system_prompt: str,
        model: str | None = None,
        timeout_s: float | None = None,
        agent: Any | None = None,
        channel_telegram_id: int | None = None,
    ) -> str:
        """
        Query the LLM for an unconstrained plain-text response.

        This must not apply JSON schema constraints and should still record
        usage logging consistently with other query paths.
        """
        ...
