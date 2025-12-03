# llm/base.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

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


# --- Base LLM class ---


class LLM(ABC):
    """Abstract base class for all LLM implementations."""

    prompt_name: str = "Default"

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
    ) -> str:
        """
        Structured query method for conversation-aware LLMs.
        Default implementation falls back to basic query method.
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
        mime_type: str | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given image.
        Uses the LLM to analyze the image and provide a detailed description.
        Raises on failures so the scheduler's retry policy can handle it.

        Args:
            image_bytes: The image data as bytes
            mime_type: Optional MIME type of the image
            timeout_s: Optional timeout in seconds for the request
        """
        ...

    @abstractmethod
    async def describe_video(
        self,
        video_bytes: bytes,
        mime_type: str | None = None,
        duration: int | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given video.
        Uses the LLM to analyze the video and provide a detailed description.
        Raises on failures so the scheduler's retry policy can handle it.

        Args:
            video_bytes: The video data as bytes
            mime_type: Optional MIME type of the video
            duration: Video duration in seconds (optional, used for validation)
            timeout_s: Optional timeout in seconds for the request
        """
        ...

    @abstractmethod
    async def describe_audio(
        self,
        audio_bytes: bytes,
        mime_type: str | None = None,
        duration: int | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """
        Return a rich, single-string description for the given audio.
        Uses the LLM to analyze the audio and provide a detailed description.
        Raises on failures so the scheduler's retry policy can handle it.

        Args:
            audio_bytes: The audio data as bytes
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
        
        Returns:
            JSON string response that matches the schema
        
        Raises:
            RuntimeError: If the LLM doesn't support JSON schema or returns invalid response
        """
        ...
