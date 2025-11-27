# utils.py
#
# Backward compatibility: Re-export from utils package.
# This file is kept for backward compatibility but new code should import from utils.* directly.

from utils import (
    coerce_to_int,
    coerce_to_str,
    format_username,
)

__all__ = ("coerce_to_int", "coerce_to_str", "format_username")
