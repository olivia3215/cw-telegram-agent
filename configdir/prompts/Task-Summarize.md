<!-- SCHEMA_TASKS: summarize -->

# Summarization Instructions

You have the ability to create and manage conversation summaries to help maintain context in long conversations.

## When Summarization is Needed

The system will automatically request summarization when there are 70 or more unsummarized messages in a conversation. When this happens, you will be asked to create or update a summary entry covering messages that are not yet summarized. Do not summarize the most recent 20 or so messages.

## How to Use Summarization

When asked to summarize, emit a `summarize` task in your JSON response:

```json
[
  {
    "kind": "summarize",
    "content": "The user discussed their vacation plans, mentioned wanting to visit Japan, and asked about travel recommendations.",
    "min_message_id": 100,
    "max_message_id": 150,
    "first_message_date": "2025-01-15",
    "last_message_date": "2025-01-20"
  }
]
```

### Required Fields

- `kind`: Must be "summarize"
- `content`: The summary text covering the specified message range
- `min_message_id`: The minimum message ID covered by this summary (required for new summaries; preserved when updating if not provided)
- `max_message_id`: The maximum message ID covered by this summary (required for new summaries; preserved when updating if not provided)

### Recommended Fields

- `first_message_date`: The date of the first message covered by this summary (ISO 8601 date format: YYYY-MM-DD). Extract from message timestamps in the conversation history. If omitted, the system will auto-extract dates from message timestamps for new summaries.
- `last_message_date`: The date of the last message covered by this summary (ISO 8601 date format: YYYY-MM-DD). Extract from message timestamps in the conversation history. If omitted, the system will auto-extract dates from message timestamps for new summaries.

### Optional Fields

- `id`: A unique identifier. Use an existing ID to update a summary, or omit/use a new ID to create a new summary entry
- `created`: Optional creation timestamp (ISO 8601 date or date-time)

**Note:** When updating existing summaries, if you omit `min_message_id`, `max_message_id`, `first_message_date`, or `last_message_date`, the existing values will be preserved automatically. This allows you to update just the content without re-specifying the message range or dates.

## Summary Guidelines

- Summaries should cover all but the most recent 20-70 messages
- Only summarize earlier messages if they are not yet summarized
- Be concise but comprehensive - capture the key points and context
- Focus on important information that will be useful for future conversations
- Include relevant details about topics discussed, decisions made, and context established
- When updating an existing summary, you can modify it to incorporate new information or correct inaccuracies
- To delete a summary, set `content` to an empty string

## Summary Management

- Each summary entry covers a range of message IDs
- Summaries can overlap or be adjacent - the system manages them automatically
- When creating a new summary, ensure the message ID range accurately reflects which messages are covered

## Consolidating summaries

From time to time you will be asked to **consolidate summaries**. You do this by editing existing summaries, or by deleting several summaries and adding a new one to replace the deleted content. When consolidating summaries, do not summarize conversation content that appears outside the summaries. Only revise what is already summarized.

- You can edit existing summaries by using their ID and providing updated content
- You can delete existing summaries by using their ID and an empty content string.
- You can create new summaries by using a new ID or omitting the ID.
