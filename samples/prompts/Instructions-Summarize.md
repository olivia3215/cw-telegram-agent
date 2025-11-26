# Summarization Task

You need to create or update a summary entry for the conversation history below.

The messages shown below are NOT yet summarized. Create summaries that cover these messages.

Each summary entry must include:
- `content`: The summary text
- `min_message_id`: The minimum message ID covered by this summary
- `max_message_id`: The maximum message ID covered by this summary
- `first_message_date`: The date of the first message covered by this summary (ISO 8601 date format: YYYY-MM-DD). Extract from message timestamps in the conversation history.
- `last_message_date`: The date of the last message covered by this summary (ISO 8601 date format: YYYY-MM-DD). Extract from message timestamps in the conversation history.

Optional fields:
- `id`: A unique identifier (use an existing ID to update an existing summary, or omit to create a new one)

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

## Task Identifiers and Revisions

- `id` values are optional. Any string is allowed.
- Reuse the same `id` to replace a previous task. When the runtime sees a new task
  with an existing `id`, it removes the earlier task before adding the new one.
- To cancel a task, emit a `think` task with the same `id`. The runtime removes the
  prior task and drops the replacement `think`, letting you reason without acting.

## Supported Task Types

All tasks automatically receive `agent_id` and `channel_id` context when executed;
you do not need to supply them.

### `think`
- Purpose: internal reasoning. The content is never shown to the user.
- Fields: `text` (string).
- Think freely to plan, review conversation history, or explain your summarization approach.

### `summarize`
- Creates or updates a conversation summary entry.
- Fields:
  - `id`: Optional summary identifier for updating or deleting an existing summary entry.
  - `content`: Summary text covering the specified message range. Use an empty string to delete an existing summary.
  - `min_message_id`: The minimum message ID covered by this summary (required).
  - `max_message_id`: The maximum message ID covered by this summary (required).
  - `first_message_date`: The date of the first message covered by this summary (required, ISO 8601 date format: YYYY-MM-DD). Extract from message timestamps in the conversation history.
  - `last_message_date`: The date of the last message covered by this summary (required, ISO 8601 date format: YYYY-MM-DD). Extract from message timestamps in the conversation history.
- Each summary entry covers a range of message IDs. You can create new summaries or update existing ones by using their ID.
- Always include the dates of the first and last messages covered by extracting them from the message timestamps in the conversation history.

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
- When updating an existing summary, you can modify it to incorporate new information or correct inaccuracies.
- Ensure the message ID range accurately reflects which messages are covered by the summary.

# Metadata

Conversation turns appearing in the conversation history include metadata such as sender, message ID, and timestamps. Only record in the summary what is necessary to make sense of it later.
