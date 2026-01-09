<!-- SCHEMA_TASKS: summarize, think -->

# Summarization Task

You need to create or update a summary entry for the conversation history below.

The messages shown below are NOT yet summarized. Create summaries that cover these messages.

Each summary entry must include:
- `content`: The summary text
- `min_message_id`: The minimum message ID covered by this summary (required)
- `max_message_id`: The maximum message ID covered by this summary (required)

Recommended fields (will be auto-extracted if omitted for new summaries):
- `first_message_date`: The date of the first message covered by this summary (ISO 8601 date format: YYYY-MM-DD). Extract from message timestamps in the conversation history. If omitted, the system will auto-extract dates from message timestamps.
- `last_message_date`: The date of the last message covered by this summary (ISO 8601 date format: YYYY-MM-DD). Extract from message timestamps in the conversation history. If omitted, the system will auto-extract dates from message timestamps.

Optional fields:
- `id`: A unique identifier

Each `summarize` should summarize one logical conversation. If part of the conversation is unimportant or of no interest to you, there is no need to summarize it.

# Response Format: JSON Tasks

- Output a JSON array (`[...]`) containing task objects in the order they should run.
- Your reply **must** be a single JSON array of task objects, nothing more or less.
- You should never produce an empty response. If you decide not to act, emit one
`think` task explaining why.

## Example

```json
[
  {
    "kind": "think",
    "id": "think-1",
    "text": "Reviewing the conversation history to identify key topics and themes."
  },
  {
    "kind": "summarize",
    "id": "summary-1",
    "content": "Peter (1234) discussed their vacation plans, mentioned wanting to visit Japan, and asked about travel recommendations.",
    "min_message_id": 100,
    "max_message_id": 150,
    "first_message_date": "2025-01-15",
    "last_message_date": "2025-01-20"
  }
]
```

## Supported Task Types

### `think`
- Purpose: internal reasoning. The content is never shown to the user.
- Fields: `text` (string).
- Think freely to plan, review conversation history, or explain your summarization approach.

### `summarize`
- Creates or updates a conversation summary entry.
- Fields:
  - `content`: Summary text covering the specified message range. Use an empty string to delete an existing summary.
  - `min_message_id`: The minimum message ID covered by this summary (required for new summaries; preserved when updating if not provided).
  - `max_message_id`: The maximum message ID covered by this summary (required for new summaries; preserved when updating if not provided).
  - `first_message_date`: The date of the first message covered by this summary (recommended, ISO 8601 date format: YYYY-MM-DD). Extract from message timestamps in the conversation history. If omitted, the system will auto-extract dates from message timestamps for new summaries.
  - `last_message_date`: The date of the last message covered by this summary (recommended, ISO 8601 date format: YYYY-MM-DD). Extract from message timestamps in the conversation history. If omitted, the system will auto-extract dates from message timestamps for new summaries.
- Each summary entry covers a range of message IDs.
- When creating summaries, include the message IDs and dates of the first and last messages covered by extracting them from the message timestamps in the conversation history.

# Thinking Instructions

Use `think` tasks to:
- Plan your summarization approach.
- Review conversation history to identify key themes and topics.
- Explain your reasoning for what to include in the summary.
- Consider whether to update an existing summary or create a new one.

Think tasks are dropped before execution. You may include as many as needed, before,
between, or after other tasks.

# General Guidance

- Create concise but comprehensive summaries that capture key points and context.
- Focus on important information that will be useful for future conversations.
- Include relevant details about topics discussed, decisions made, and context established.
- Ensure the message ID range accurately reflects which messages are covered by the summary.
- Summarize only information that is relevant to you. Exclude smalltalk, greetings, etc.

# Metadata

Conversation turns appearing in the conversation history include metadata such as sender, message ID, and timestamps. Only record in the summary what is necessary to make sense of it later.
