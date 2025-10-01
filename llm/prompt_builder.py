# llm/prompt_builder.py

# Copyright (c) 2025 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.

import logging
from collections.abc import Iterable
from typing import Any

from .base import ChatMsg, MsgPart

logger = logging.getLogger(__name__)


def _mk_text_part(text: str) -> dict[str, str]:
    return {"text": text}


def _normalize_parts_for_message(
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

    # 1) Metadata header (always, per spec)
    if not is_agent:
        header_bits: list[str] = []
        who = m.get("sender") or ""
        sid = m.get("sender_id") or ""
        if who and sid:
            header_bits.append(f'sender="{who}" sender_id={sid}')
        elif who or sid:
            header_bits.append(f"sender_id={who or sid}")
        if m.get("msg_id"):
            header_bits.append(f'message_id={m["msg_id"]}')
        if header_bits:
            parts.append(_mk_text_part(f"[metadata] {' '.join(header_bits)}"))

    # 2) Original message content in original order
    raw_parts: list[MsgPart] | None = m.get("parts")

    if raw_parts is not None and len(raw_parts) > 0:
        for p in raw_parts:
            k = (p.get("kind") or "").lower()
            if k == "text":
                txt = (p.get("text") or "").strip()
                if txt:
                    parts.append(_mk_text_part(txt))
            elif k == "media":
                rendered = (p.get("rendered_text") or "").strip()
                if rendered:
                    parts.append(_mk_text_part(rendered))
                else:
                    # Fallback: brief placeholder so the LLM knows something was here.
                    mk = (p.get("media_kind") or "media").strip()
                    uid = (p.get("unique_id") or "").strip()
                    placeholder = f"[{mk} present" + (f" uid={uid}]" if uid else "]")
                    parts.append(_mk_text_part(placeholder))
            else:
                # Unknown part type: surface minimally instead of dropping.
                parts.append(_mk_text_part(f"[{k or 'unknown'} part]"))
    else:
        # Fallback: single text
        fallback = (m.get("text") or "").strip()
        if fallback:
            parts.append(_mk_text_part(fallback))

    return parts


def build_gemini_contents(
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
        logger.info(f"=====> HISTORY ITEM: {m}")
        is_agent = bool(m.get("is_agent"))
        any_user_messages = any_user_messages or not is_agent
        any_agent_messages = any_agent_messages or is_agent
        role = "assistant" if is_agent else "user"
        parts = _normalize_parts_for_message(
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
            special_user_message = "[special] This is the beginning of a conversation with Michael Duboy. Please respond with your first message."
        contents.append(
            {"role": "user", "parts": [_mk_text_part(special_user_message)]}
        )

    return contents
