# handlers/__init__.py

# Import all handlers to ensure they are registered
from . import (
    block,  # noqa: F401
    clear_conversation,  # noqa: F401
    received,  # noqa: F401
    send,  # noqa: F401
    sticker,  # noqa: F401
    unblock,  # noqa: F401
    wait,  # noqa: F401
)
