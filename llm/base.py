# llm/base.py

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import TypedDict

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
    set_name: str | None
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
      - msg_id:    message id string (if available)
      - is_agent:  True if this message was sent by *our* agent persona
      - ts_iso:    optional ISO-8601 timestamp (trace only; not shown to model)
    """

    sender: str
    sender_id: str
    parts: list[MsgPart]
    text: str
    is_agent: bool
    msg_id: str | None
    ts_iso: str | None


# --- Base LLM class ---


class LLM(ABC):
    """Abstract base class for all LLM implementations."""

    prompt_name: str = "Default"

    @abstractmethod
    async def query(self, system_prompt: str, user_prompt: str) -> str:
        """
        Basic query method for simple system + user prompt.
        """
        pass

    async def query_structured(
        self,
        *,
        persona_instructions: str,
        role_prompt: str | None,
        llm_specific_prompt: str | None,
        now_iso: str,
        chat_type: str,  # "direct" | "group"
        curated_stickers: Iterable[str] | None,
        history: Iterable[ChatMsg],
        target_message: ChatMsg | None,
        history_size: int = 500,
        include_speaker_prefix: bool = True,
        include_message_ids: bool = True,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> str:
        """
        Structured query method for conversation-aware LLMs.
        Default implementation falls back to basic query method.
        """
        # Default implementation: fallback to basic query
        # Subclasses can override this for more sophisticated handling
        system_prompt = persona_instructions
        if role_prompt:
            system_prompt += f"\n\n{role_prompt}"
        if llm_specific_prompt:
            system_prompt += f"\n\n{llm_specific_prompt}"

        # Simple conversion of history to text
        user_prompt = f"Current time: {now_iso}\nChat type: {chat_type}\n\n"
        if target_message:
            # Extract text from parts if available, otherwise fall back to text field
            message_text = ""
            parts = target_message.get("parts")
            if parts:
                # Extract text from all text parts
                text_parts = []
                for part in parts:
                    if part.get("kind") == "text" and part.get("text"):
                        text_parts.append(part["text"])
                message_text = " ".join(text_parts)
            else:
                message_text = target_message.get("text", "")
            user_prompt += f"Latest message: {message_text}"

        return await self.query(system_prompt, user_prompt)
