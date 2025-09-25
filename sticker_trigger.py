# sticker_trigger.py

"""
Parser for the sticker trigger block emitted by the LLM.

Canonical SPEC:
  (no reply)
    # «sticker»

    WendyDancer
    😀

  (with reply)
    # «sticker» 1234

    WendyDancer
    😘

Rules:
- Header MUST be exactly "«sticker»" (with guillemets) after a '#' markdown header.
- Optional decimal reply target may appear on the header line.
- After the header, skip any number of empty/whitespace-only lines.
- Next non-empty line = SET short name (e.g., WendyDancer).
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


def parse_sticker_body(
    body: str,
    *,
    allow_missing_set_during_transition: bool = False,
) -> tuple[str | None, str] | None:
    """
    Parse the body of a «sticker» block (header already handled elsewhere).

    Input examples (whitespace/blank lines are allowed and ignored):
        WendyDancer
        😀

        # During transition, the set line may be omitted:
        😀
    Returns:
        (set_short_name, sticker_name)
        - set_short_name is None only during the transition window when the set
          line is omitted.
        - Returns None if no valid name line is found.
    """
    # Normalize to first two non-empty lines
    lines = [ln.strip() for ln in body.splitlines()]
    lines = [ln for ln in lines if ln]  # drop empty lines

    if not lines:
        return None

    if len(lines) == 1:
        # Old behavior: only the name line present
        if allow_missing_set_during_transition:
            return (None, lines[0])
        return None

    # Two or more lines: take the first as set, second as name
    set_line = lines[0]
    name_line = lines[1]
    return (set_line, name_line)
