# sticker_trigger.py

"""
Parser for the sticker trigger block emitted by the LLM.

Canonical SPEC:
  (no reply)
    # «sticker»

    WendyAI
    😀

  (with reply)
    # «sticker» 1234

    WendyAI
    😘

Rules:
- Header MUST be exactly "«sticker»" (with guillemets) after a '#' markdown header.
- Optional decimal reply target may appear on the header line.
- After the header, skip any number of empty/whitespace-only lines.
- Next non-empty line = SET short name (e.g., WendyAI).
- Next non-empty line = STICKER name (emoji or short name).
- Leading/trailing spaces on those lines are ignored.
- During development ONLY we allow "missing set line" (old behavior) where
  only one non-empty line follows the header; in that case it is taken as the
  sticker NAME and set is None. This will be disabled before merge.

This module is PURE parsing; no Telegram/Telethon calls here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class StickerTrigger:
    set_short_name: str | None  # None only during transition window (missing set line)
    sticker_name: str
    reply_to_message_id: int | None


# Header pattern:
#   # «sticker»
#   # «sticker» 1234
_HEADER_RE = re.compile(
    r"""
    (?m)                # multiline
    ^[ \t]*\#           # leading spaces + markdown '#'
    [ \t]+              # at least one space
    «sticker»           # exact token with guillemets
    (?:[ \t]+(\d+))?    # optional reply id (digits)
    [ \t]*$             # trailing spaces until EOL
    """,
    re.VERBOSE,
)


def parse_first_sticker_trigger(
    text: str,
    *,
    allow_missing_set_during_transition: bool = True,
) -> StickerTrigger | None:
    """
    Find and parse the FIRST sticker trigger block in `text`.

    Returns:
        StickerTrigger if a well-formed block is found, else None.
    """
    m = _HEADER_RE.search(text)
    if not m:
        return None

    reply_to: int | None
    reply_str = m.group(1)
    reply_to = int(reply_str) if reply_str is not None else None

    # Slice the text starting just after the matched header line
    tail = text[m.end() :]

    set_line: str | None = None
    name_line: str | None = None

    for raw in tail.splitlines():
        line = raw.strip()
        if not line:
            continue  # Skip empty/whitespace-only lines
        if set_line is None:
            set_line = line
            continue
        if name_line is None:
            name_line = line
            break  # We have both lines; stop scanning

    # Transitional old form (single line after header)
    if name_line is None:
        if allow_missing_set_during_transition and set_line:
            return StickerTrigger(
                set_short_name=None, sticker_name=set_line, reply_to_message_id=reply_to
            )
        return None

    return StickerTrigger(
        set_short_name=set_line, sticker_name=name_line, reply_to_message_id=reply_to
    )
