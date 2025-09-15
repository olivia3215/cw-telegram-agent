# media_injector.py

from typing import Sequence, Any

from telegram_media import iter_media_parts  # imported for future use
from media_cache import MediaCache           # imported for future use
from media_types import MediaItem            # imported for type context

def inject_media_descriptions(messages: Sequence[Any]) -> Sequence[Any]:
    """
    Placeholder hook for the received-task history builder.

    Eventually:
      - detect media via iter_media_parts(msg)
      - look up / generate descriptions
      - return a structure with media replaced by text

    For now, it returns the input unchanged so tests/behavior are unaffected.
    """
    return messages
