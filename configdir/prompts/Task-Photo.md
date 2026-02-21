<!-- SCHEMA_TASKS: send_media -->

# Send Media Task

You can send curated media from your saved messages using the `send_media` task.
Supported types include photos, audio, music, video, movies, stickers that don't have
a set name, and other documents. Media are referenced by their `unique_id`.

(Stickers that belong to a named set should be sent with the `sticker` task instead.)

## How to Use the Send Media Task

To send an item, use the `send_media` task with the item's `unique_id`. The `unique_id`
is a stable identifier that uniquely identifies each piece of media. You can find available
media and their `unique_id` values listed in the system prompt under "Media you may send
using a `send_media` task". Each entry shows the kind (e.g. photo, audio, video, sticker)
and optionally a description.

## Syntax

```json
[
  {
    "kind": "send_media",
    "unique_id": "<media_unique_id>",
    "reply_to": <optional_message_id>
  }
]
```

- `kind`: Must be `"send_media"` to send media.
- `unique_id` (required): The Telegram `file_unique_id` string for the media you want to send.
  This can be found in the list of available media in your system prompt.
- `reply_to` (optional): The message ID to reply to. If provided, the media will be sent as a reply.

## Example

If you want to send media with `unique_id` "ABC123XYZ" as a reply to message 42:

```json
[
  {
    "kind": "send_media",
    "unique_id": "ABC123XYZ",
    "reply_to": 42
  }
]
```

## Important Notes

- **Always use the `send_media` task to send these items, never use the `send` task.**
- The `unique_id` is case-sensitive and must match exactly.
- Descriptions and kind (photo, audio, video, sticker, etc.) are shown in the system prompt to help you choose the right item.
