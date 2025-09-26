# llm/base.py

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, TypedDict


class MsgPart(TypedDict, total=False):
    # text part
    kind: str  # "text" | "media" | other future kinds
    text: str

    # media part
    media_kind: str  # e.g., "sticker", "photo", "gif", "audio", ...
    rendered_text: str  # pre-rendered compact text for the media
    unique_id: str  # stable identifier for the media


class ChatMsg(TypedDict, total=False):
    """
    Normalized view of a chat message for building LLM history.
    Exactly what tests already exercise.

    Content (one of):
      - parts: list[MsgPart]  (preferred)
      - text: str             (fallback if 'parts' missing)

    Identity / trace:
      - sender: str
      - sender_id: str
      - msg_id: str
      - is_agent: bool
    """

    parts: list[MsgPart]
    text: str

    sender: str
    sender_id: str
    msg_id: str
    is_agent: bool


class LLM(Protocol):
    async def query_structured(  # pragma: no cover (interface)
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
    ) -> str: ...


def _header_part(sender: str, sender_id: str, msg_id: str) -> dict:
    return {
        "text": f"From: {sender} ({sender_id}) — id: {msg_id}",
    }


def _render_msg_parts(msg: ChatMsg) -> list[dict]:
    """
    Convert our message parts to Gemini text parts (no binary).
    - text parts pass through
    - media parts use 'rendered_text' if available, else a placeholder
    """
    out: list[dict] = []
    parts = msg.get("parts") or []
    for p in parts:
        kind = p.get("kind")
        if kind == "text":
            t = p.get("text", "")
            if t:
                out.append({"text": t})
        else:
            # media or unknown: prefer rendered_text; else placeholder
            rt = p.get("rendered_text")
            if isinstance(rt, str) and rt.strip():
                out.append({"text": rt})
            else:
                mk = p.get("media_kind", "media")
                # out.append({"text": f"[media: {mk} not understood]"})
                out.append({"text": f"[{mk} present]"})
    # Fallback: if no parts but legacy 'text' exists
    if not out and "text" in msg and isinstance(msg["text"], str) and msg["text"]:
        out.append({"text": msg["text"]})
    return out


def build_llm_contents(
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
) -> list[dict]:
    """
    Produces:
      [ system_turn, ...history (capped), target? ]

    - system turn carries persona/role/model notes, time, chat type, curated stickers
    - history is chronological (oldest→newest), with agent turns role='assistant'
      and non-agent turns role='user'. Non-agent turns get a header part.
    - target is appended last as a user turn (no special casing beyond header).
    """
    # --- System turn (single) ---
    sys_lines: list[str] = []
    if persona_instructions:
        sys_lines.append(str(persona_instructions).strip())
    if role_prompt:
        sys_lines.append("Role Prompt:")
        sys_lines.append(str(role_prompt).strip())
    if llm_specific_prompt:
        sys_lines.append("LLM-Specific Prompt:")
        sys_lines.append(str(llm_specific_prompt).strip())

    # Current time and chat type
    sys_lines.append(f"Current time: {now_iso}")
    sys_lines.append(f"Chat type: {chat_type}")

    # Curated stickers list (if provided)
    if curated_stickers:
        sys_lines.append("Curated stickers available:")
        for s in curated_stickers:
            sys_lines.append(f"- {s}")

    system_turn = {
        "role": "system",
        "parts": [{"text": "\n\n".join([s for s in sys_lines if s])}],
    }

    # --- History capping ---
    hist_list = list(history)
    if history_size is not None and len(hist_list) > history_size:
        hist_list = hist_list[-history_size:]

    # --- Convert history messages ---
    out: list[dict] = [system_turn]
    for msg in hist_list:
        is_agent = bool(msg.get("is_agent"))
        role = "assistant" if is_agent else "user"
        parts_out: list[dict] = []

        # Non-agent messages: add a small header
        if not is_agent and include_speaker_prefix:
            parts_out.append(
                _header_part(
                    msg.get("sender", "Unknown"),
                    msg.get("sender_id", "unknown"),
                    msg.get("msg_id", ""),
                )
            )

        parts_out.extend(_render_msg_parts(msg))
        out.append({"role": role, "parts": parts_out})

    # --- Append target as last user turn ---
    if target_message:
        t_parts: list[dict] = []
        if include_speaker_prefix:
            t_parts.append(
                _header_part(
                    target_message.get("sender", "Unknown"),
                    target_message.get("sender_id", "unknown"),
                    target_message.get("msg_id", ""),
                )
            )
        t_parts.extend(_render_msg_parts(target_message))
        out.append({"role": "user", "parts": t_parts})

    return out


# Back-compat alias for existing imports/tests:
def build_gemini_contents(
    *,
    persona_instructions: str,
    role_prompt: str | None,
    llm_specific_prompt: str | None,
    now_iso: str,
    chat_type: str,
    curated_stickers: Iterable[str] | None,
    history: Iterable[ChatMsg],
    target_message: ChatMsg | None,
    history_size: int = 500,
    include_speaker_prefix: bool = True,
    include_message_ids: bool = True,
) -> list[dict]:
    return build_llm_contents(
        persona_instructions=persona_instructions,
        role_prompt=role_prompt,
        llm_specific_prompt=llm_specific_prompt,
        now_iso=now_iso,
        chat_type=chat_type,
        curated_stickers=curated_stickers,
        history=history,
        target_message=target_message,
        history_size=history_size,
        include_speaker_prefix=include_speaker_prefix,
        include_message_ids=include_message_ids,
    )
