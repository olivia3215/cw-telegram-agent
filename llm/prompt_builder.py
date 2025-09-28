# llm/prompt_builder.py

from collections.abc import Iterable
from typing import Any

from .base import ChatMsg, MsgPart


def _mk_text_part(text: str) -> dict[str, str]:
    return {"text": text}


def _normalize_parts_for_message(
    m: ChatMsg,
    *,
    include_speaker_prefix: bool,
    include_message_ids: bool,
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
        if include_speaker_prefix:
            who = m.get("sender") or ""
            sid = m.get("sender_id") or ""
            if who and sid:
                header_bits.append(f'sender="{who}" sender_id={sid}')
            elif who or sid:
                header_bits.append(f"sender_id={who or sid}")
        if include_message_ids and m.get("msg_id"):
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
    *,
    # System turn inputs
    persona_instructions: str,
    role_prompt: str | None,
    llm_specific_prompt: str | None,
    now_iso: str,
    chat_type: str,  # "direct" | "group" (stringly typed to avoid import cycles)
    curated_stickers: Iterable[str] | None = None,
    # History & target
    history: Iterable[ChatMsg],
    target_message: ChatMsg | None,  # message we want the model to respond to
    history_size: int = 500,
    # Formatting toggles
    include_speaker_prefix: bool = True,
    include_message_ids: bool = True,
) -> list[dict[str, Any]]:
    """
    Construct Gemini 'contents' with roles and multi-part messages:
      - One 'system' turn: persona + role prompt + model-specific prompt + metadata + target instruction
      - Chronological 'user'/'assistant' turns for prior messages (bounded by history_size),
        each with an ordered list of 'parts' (metadata header first, then content parts).
      - Target message is NOT appended as a separate turn; instead, a system instruction
        is added to respond to the specific message.

    Pure function: no I/O, no network, no mutation of inputs.
    """
    # --- 1) System turn ---
    sys_lines: list[str] = []
    if persona_instructions:
        sys_lines.append(persona_instructions.strip())
    if role_prompt:
        sys_lines.append("\n# Role Prompt\n" + role_prompt.strip())
    if llm_specific_prompt:
        sys_lines.append("\n# Model-Specific Guidance\n" + llm_specific_prompt.strip())
    sys_lines.append(f"\n# Context\nCurrent time: {now_iso}\nChat type: {chat_type}")
    if curated_stickers:
        sticker_list = ", ".join(curated_stickers)
        sys_lines.append(f"Curated stickers available: {sticker_list}")

    # Add target message instruction if provided
    if target_message is not None and target_message.get("msg_id"):
        sys_lines.append(
            f"\n# Target Message\nConsider responding to message with message_id {target_message['msg_id']}."
        )

    contents: list[dict[str, Any]] = [
        {"role": "system", "parts": [_mk_text_part("\n\n".join(sys_lines).strip())]}
    ]

    # --- 2) Chronological history (bounded) ---
    hist_list = list(history)
    if history_size >= 0:
        hist_list = hist_list[-history_size:]

    for m in hist_list:
        is_agent = bool(m.get("is_agent"))
        role = "assistant" if is_agent else "user"
        parts = _normalize_parts_for_message(
            m,
            include_speaker_prefix=include_speaker_prefix,
            include_message_ids=include_message_ids,
            is_agent=is_agent,
        )
        if parts:
            contents.append({"role": role, "parts": parts})

    # Note: Target message is no longer appended as a separate turn
    return contents
