<!-- SCHEMA_TASKS: event -->

# Event (scheduled action) instructions

You can schedule actions to run in the future using the `event` task. Events are channel-specific and can fire once or recur at an interval.

## Creating an event

Provide `intent` (what to do when the event fires) and `time` (when to fire). Time is interpreted in your timezone unless you set `timezone` (e.g. `America/New_York`). Optional: `interval` for recurrence (e.g. `1 hours`, `30 minutes`, `1 days`, `1 weeks`) and `occurrences` (how many times to recur; omit for unlimited until deleted).

```json
[
  {
    "kind": "event",
    "intent": "Reply to Alice about the meeting.",
    "time": "2026-03-01T14:00:00"
  }
]
```

With recurrence (every 2 hours, 5 times):

```json
[
  {
    "kind": "event",
    "intent": "Check in on the project.",
    "time": "2026-03-01T09:00:00",
    "interval": "2 hours",
    "occurrences": 5
  }
]
```

## Updating an event

Reuse the same `id` with new fields to update:

```json
[
  {
    "kind": "event",
    "id": "event-abc123",
    "intent": "Reply to Alice (updated).",
    "time": "2026-03-01T15:00:00"
  }
]
```

## Deleting an event

Use the event's `id` with empty `intent` to delete it:

```json
[
  {
    "kind": "event",
    "id": "event-abc123",
    "intent": ""
  }
]
```

## Fields

- **intent** (required for create): Text instruction for your future self when the event fires. Empty string with existing `id` deletes the event.
- **time** (required for create): When to fire. ISO 8601 date-time; if no timezone offset, your agent timezone is used unless **timezone** is set. If **timezone** is set, it is always used to interpret **time** (e.g. `2026-02-28T09:30:00Z` with **timezone** `America/Los_Angeles` means 09:30 in Los Angeles).
- **timezone** (optional): IANA timezone for interpreting **time** (e.g. `Europe/London`). If omitted, your configured timezone is used. When set, it overrides any offset in **time** (Z or +00:00); the date and time are read as local time in this zone, then converted to UTC.
- **interval** (optional): Recurrence step: a number and unit, e.g. `1 hours`, `30 minutes`, `1 days`, `1 weeks`. Singular or plural accepted.
- **occurrences** (optional): Number of times to recur. If omitted and **interval** is set, the event recurs until you delete it.

## Best practices

- Use events for reminders and delayed follow-ups in this channel.
- Keep intent clear so your future self knows what to do.
- Delete or update events when they are no longer needed.
- When using **timezone**, use a zone-naive **time** (e.g. `2026-02-28T09:30:00`) for clarity; if **time** includes Z or an offset, **timezone** still wins and the time is interpreted in that zone.
