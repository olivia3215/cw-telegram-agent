from __future__ import annotations

from typing import Any, Dict

import copy

_TASK_RESPONSE_SCHEMA_DICT: Dict[str, Any] = {
    "title": "Task List",
    "description": (
        "Ordered list of task objects for the Telegram agent. Emit every action as a "
        "separate object in this array."
    ),
    "type": "array",
    "items": {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["think"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Optional identifier to delete a previous task.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Private reasoning or planning text. Never sent to the human user.",
                    },
                },
                "required": ["kind", "text"],
                "additionalProperties": False,
                "title": "Think Task",
                "description": "Internal reasoning task; output is discarded before reaching the user.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["send"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Recommended identifier so later tasks can revise or cancel this message.",
                    },
                    "text": {
                        "type": "string",
                        "description": "Markdown-formatted message to send to the current conversation.",
                    },
                    "reply_to": {
                        "anyOf": [
                            {"type": "integer"},
                            {"type": "string", "minLength": 1},
                        ],
                        "description": "Optional Telegram message ID to reply to.",
                    },
                },
                "required": ["kind", "text"],
                "additionalProperties": False,
                "title": "Send Task",
                "description": "Send a Markdown-formatted Telegram message.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["react"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Optional identifier so later tasks can revise or cancel this reaction.",
                    },
                    "emoji": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Emoji to react with. Use standard Unicode emoji characters.",
                    },
                    "message_id": {
                        "anyOf": [
                            {"type": "integer"},
                            {"type": "string", "minLength": 1},
                        ],
                        "description": "Telegram message ID to target with the reaction.",
                    },
                },
                "required": ["kind", "emoji", "message_id"],
                "additionalProperties": False,
                "title": "React Task",
                "description": "Add an emoji reaction to a specific Telegram message.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["sticker"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Optional identifier for later revisions.",
                    },
                    "sticker_set": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Telegram sticker set short name (e.g., 'WendyDancer').",
                    },
                    "name": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Sticker name or emoji within the set.",
                    },
                    "reply_to": {
                        "anyOf": [
                            {"type": "integer"},
                            {"type": "string", "minLength": 1},
                        ],
                        "description": "Optional Telegram message ID to reply to.",
                    },
                },
                "required": ["kind", "sticker_set", "name"],
                "additionalProperties": False,
                "title": "Sticker Task",
                "description": "Send a sticker from a known Telegram sticker set.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["photo"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Optional identifier for later revisions.",
                    },
                    "unique_id": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Telegram file_unique_id string for the photo from saved messages.",
                    },
                    "reply_to": {
                        "anyOf": [
                            {"type": "integer"},
                            {"type": "string", "minLength": 1},
                        ],
                        "description": "Optional Telegram message ID to reply to.",
                    },
                },
                "required": ["kind", "unique_id"],
                "additionalProperties": False,
                "title": "Photo Task",
                "description": "Send a curated photo from saved messages by file_unique_id.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["wait"],
                        "description": "Task type identifier; always 'wait'.",
                    },
                    "delay": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Delay duration in seconds before following tasks may run.",
                    },
                },
                "required": ["kind", "delay"],
                "additionalProperties": False,
                "title": "Wait Task",
                "description": "Insert a delay before subsequent tasks run.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["block"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Optional identifier for later revision.",
                    },
                },
                "required": ["kind"],
                "additionalProperties": False,
                "title": "Block Task",
                "description": "Block the current direct-message peer.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["unblock"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Optional identifier for later revision.",
                    },
                },
                "required": ["kind"],
                "additionalProperties": False,
                "title": "Unblock Task",
                "description": "Unblock a previously blocked direct-message peer.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["remember"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Optional memory identifier for updating or deleting an existing memory entry.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Memory text to store. Use an empty string to delete an existing memory.",
                    },
                    "created": {
                        "anyOf": [
                            {
                                "type": "string",
                                "format": "date-time",
                            },
                            {
                                "type": "string",
                                "format": "date",
                            },
                        ],
                        "description": "Optional creation timestamp (ISO 8601 date or date-time).",
                    },
                },
                "required": ["kind"],
                "additionalProperties": False,
                "title": "Remember Task",
                "description": "Create, update, or delete a persistent memory entry.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["intend"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Optional intention identifier for updating or deleting an existing intention entry.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Intention text to store. Use an empty string to delete an existing intention.",
                    },
                    "created": {
                        "anyOf": [
                            {
                                "type": "string",
                                "format": "date-time",
                            },
                            {
                                "type": "string",
                                "format": "date",
                            },
                        ],
                        "description": "Optional creation timestamp (ISO 8601 date or date-time).",
                    },
                },
                "required": ["kind", "content"],
                "additionalProperties": False,
                "title": "Intend Task",
                "description": "Create, update, or delete a global intention entry.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["plan"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Optional plan identifier for updating or deleting an existing plan entry.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Channel-specific plan text to store. Use an empty string to delete an existing plan.",
                    },
                    "created": {
                        "anyOf": [
                            {
                                "type": "string",
                                "format": "date-time",
                            },
                            {
                                "type": "string",
                                "format": "date",
                            },
                        ],
                        "description": "Optional creation timestamp (ISO 8601 date or date-time).",
                    },
                },
                "required": ["kind", "content"],
                "additionalProperties": False,
                "title": "Plan Task",
                "description": "Create, update, or delete a channel-specific plan entry.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["summarize"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Optional summary identifier for updating or deleting an existing summary entry.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Summary text covering the specified message range. Use an empty string to delete an existing summary.",
                    },
                    "min_message_id": {
                        "anyOf": [
                            {"type": "integer"},
                            {"type": "string"},
                        ],
                        "description": (
                            "The minimum message ID covered by this summary. "
                            "Required for new summaries. When updating existing summaries, "
                            "this value is preserved if not provided, allowing content-only updates."
                        ),
                    },
                    "max_message_id": {
                        "anyOf": [
                            {"type": "integer"},
                            {"type": "string"},
                        ],
                        "description": (
                            "The maximum message ID covered by this summary. "
                            "Required for new summaries. When updating existing summaries, "
                            "this value is preserved if not provided, allowing content-only updates."
                        ),
                    },
                    "created": {
                        "anyOf": [
                            {
                                "type": "string",
                                "format": "date-time",
                            },
                            {
                                "type": "string",
                                "format": "date",
                            },
                        ],
                        "description": "Optional creation timestamp (ISO 8601 date or date-time).",
                    },
                    "first_message_date": {
                        "anyOf": [
                            {
                                "type": "string",
                                "format": "date",
                            },
                        ],
                        "description": (
                            "Optional date of the first message covered by this summary (ISO 8601 date format: YYYY-MM-DD). "
                            "If omitted for new summaries, dates are auto-extracted from message timestamps. "
                            "When updating existing summaries, dates are preserved if not provided."
                        ),
                    },
                    "last_message_date": {
                        "anyOf": [
                            {
                                "type": "string",
                                "format": "date",
                            },
                        ],
                        "description": (
                            "Optional date of the last message covered by this summary (ISO 8601 date format: YYYY-MM-DD). "
                            "If omitted for new summaries, dates are auto-extracted from message timestamps. "
                            "When updating existing summaries, dates are preserved if not provided."
                        ),
                    },
                },
                "required": ["kind", "content", "min_message_id", "max_message_id"],
                "additionalProperties": False,
                "title": "Summarize Task",
                "description": "Create, update, or delete a conversation summary entry.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["retrieve"],
                    },
                    "urls": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "minItems": 1,
                        "maxItems": 3,
                        "description": "List of HTTP or HTTPS URLs to fetch (maximum three per task).",
                    },
                },
                "required": ["kind", "urls"],
                "additionalProperties": False,
                "title": "Retrieve Task",
                "description": "Request retrieval of up to three web pages to augment context.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["xsend"],
                    },
                    "target_channel_id": {
                        "anyOf": [
                            {"type": "integer"},
                            {"type": "string", "minLength": 1},
                        ],
                        "description": "Telegram peer ID of the target conversation.",
                    },
                    "intent": {
                        "type": "string",
                        "description": "Optional secret instruction for the agent to send its future self in the target channel.",
                    },
                },
                "required": ["kind", "target_channel_id"],
                "additionalProperties": False,
                "title": "XSend Task",
                "description": "Send an intent to the agent's future self in another channel for later follow-up.",
            },
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["schedule"],
                    },
                    "id": {
                        "type": "string",
                        "description": "Optional activity identifier. If provided and matches existing entry, updates or deletes it. If not provided, creates new entry. Required for update/delete operations.",
                    },
                    "start_time": {
                        "type": "string",
                        "format": "date-time",
                        "description": "ISO 8601 datetime string with timezone (e.g., '2025-12-02T06:00:00-10:00'). Required for create, optional for update.",
                    },
                    "end_time": {
                        "type": "string",
                        "format": "date-time",
                        "description": "ISO 8601 datetime string with timezone. Required for create, optional for update.",
                    },
                    "activity_name": {
                        "type": "string",
                        "description": "Short human-readable name for the activity. Required for create, optional for update. If empty and id matches existing entry, deletes it.",
                    },
                    "responsiveness": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 100,
                        "description": "Agent's responsiveness level (0 = sleeping/unavailable, 100 = actively chatting). Required for create, optional for update.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Detailed description of what you'll be doing (include foods, work details, location, etc. in this field). Required for create, optional for update.",
                    },
                },
                "required": ["kind"],
                "additionalProperties": False,
                "title": "Schedule Task",
                "description": "Create, update, or delete a schedule entry. Operation determined by id and activity_name. For create: all fields except id are required. For update: only id is required, other fields are optional. For delete: id and empty activity_name are required.",
            }
        ]
    },
}


def get_task_response_schema_dict() -> Dict[str, Any]:
    """Return a JSON schema dict describing valid task responses."""

    return copy.deepcopy(_TASK_RESPONSE_SCHEMA_DICT)
