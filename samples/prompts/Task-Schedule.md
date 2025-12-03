# Schedule Management

You can manage your daily schedule by creating, updating, or deleting schedule entries using `schedule` tasks. This allows you to plan your activities, including sleep, meals, work, and leisure time.

## Creating a Schedule Entry

To add a new activity to your schedule, provide all required fields. The `id` field is optional and will be generated if not provided:

```json
[
  {
    "kind": "schedule",
    "start_time": "2025-12-02T06:00:00-10:00",
    "end_time": "2025-12-02T06:15:00-10:00",
    "activity_name": "Morning shower",
    "responsiveness": 10,
    "description": "Quick shower to wash off the salt air from sleeping with windows open"
  }
]
```

## Updating a Schedule Entry

To modify an existing schedule entry, use the same `id` with updated fields. If the `id` matches an existing entry, it will be updated:

```json
[
  {
    "kind": "schedule",
    "id": "act-abc123",
    "start_time": "2025-12-02T06:00:00-10:00",
    "end_time": "2025-12-02T06:20:00-10:00",
    "activity_name": "Morning shower",
    "responsiveness": 10,
    "description": "Quick shower to wash off the salt air"
  }
]
```

## Deleting a Schedule Entry

To remove an activity from your schedule, use the existing `id` and set `activity_name` to an empty string:

```json
[
  {
    "kind": "schedule",
    "id": "act-abc123",
    "activity_name": ""
  }
]
```

**Note:** The operation is determined automatically:
- If `id` matches an existing entry and `activity_name` is empty → **delete**
- If `id` matches an existing entry and `activity_name` is not empty → **update**
- If `id` doesn't exist or is not provided → **create**

## Schedule Entry Fields

All schedule entries require:
- `start_time`: ISO 8601 datetime string with timezone (e.g., "2025-12-02T06:00:00-10:00")
- `end_time`: ISO 8601 datetime string with timezone
- `activity_name`: Short human-readable name for the activity (e.g., "Sleep", "Morning shower", "Working at beach shack")
- `responsiveness`: Integer 0-100 indicating your availability (0 = sleeping/unavailable, 100 = actively chatting)
- `description`: Detailed description of what you'll be doing

## Best Practices

- Ensure activities don't overlap in time
- Cover the full time period you're scheduling
- Use realistic responsiveness values (0 for sleep, 10-30 for focused work, 50-80 for casual activities, 90-100 for chatting)
- Include "falling asleep" periods (15-30 minutes) before sleep activities
- Be specific with activity names and descriptions to help your future self understand what you planned
