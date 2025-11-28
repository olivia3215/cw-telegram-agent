# utils/__init__.py
#
# Shared utility functions package.
# Re-exports common utilities for backward compatibility.

from utils.type_coercion import coerce_to_int, coerce_to_str
from utils.telegram import format_username, get_channel_name, get_dialog_name, is_group_or_channel, is_dm
from utils.ids import (
    normalize_peer_id,
    extract_sticker_name_from_document,
    get_custom_emoji_name,
    extract_user_id_from_peer,
)
from utils.time import (
    TZ_ABBREVIATIONS,
    get_agent_timezone,
    normalize_created_string,
    parse_datetime_with_optional_tz,
    resolve_timezone,
    memory_sort_key,
)
from utils.markdown import flatten_node_text
from utils.formatting import (
    format_message_content_for_logging,
    strip_json_fence,
    normalize_list,
)

__all__ = [
    # Type coercion
    "coerce_to_int",
    "coerce_to_str",
    # Telegram utilities
    "format_username",
    "get_channel_name",
    "get_dialog_name",
    "is_group_or_channel",
    "is_dm",
    # ID utilities
    "normalize_peer_id",
    "extract_sticker_name_from_document",
    "get_custom_emoji_name",
    "extract_user_id_from_peer",
    # Time utilities
    "TZ_ABBREVIATIONS",
    "get_agent_timezone",
    "normalize_created_string",
    "parse_datetime_with_optional_tz",
    "resolve_timezone",
    "memory_sort_key",
    # Markdown utilities
    "flatten_node_text",
    # Formatting utilities
    "format_message_content_for_logging",
    "strip_json_fence",
    "normalize_list",
]

