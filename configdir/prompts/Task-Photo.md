<!-- SCHEMA_TASKS: photo -->

# Photo Task

You can send curated photos from your saved messages using the `photo` task.
Photos are referenced by their `unique_id`.

## How to Use the Photo Task

To send a photo, use the `photo` task with the photo's `unique_id`. The `unique_id` is a stable identifier
that uniquely identifies each photo. You can find available photos and their `unique_id` values
listed in the system prompt under "Photos you may send using a `photo` task".

## Syntax

```json
[
  {
    "kind": "photo",
    "unique_id": "<photo_unique_id>",
    "reply_to": <optional_message_id>
  }
]
```

- `kind`: Must be `"photo"` to send a photo.
- `unique_id` (required): The Telegram `file_unique_id` string for the photo you want to send.
  This can be found in the list of available photos in your system prompt.
- `reply_to` (optional): The message ID to reply to. If provided, the photo will be sent as a reply.

## Example

If you want to send a photo with `unique_id` "ABC123XYZ" as a reply to message 42:

```json
[
  {
    "kind": "photo",
    "unique_id": "ABC123XYZ",
    "reply_to": 42
  }
]
```

## Important Notes

- **Always use the `photo` task to send photos, never use the `send` task.**
- The `unique_id` is case-sensitive and must match exactly.
- Photo descriptions (if available) are shown in the system prompt to help you choose the right photo.
