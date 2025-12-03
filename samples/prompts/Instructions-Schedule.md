# Instructions - Scheduling

You are extending your daily schedule. Please create schedule entries starting from the specified start time.
The schedule should extend until midnight.

Important: The last activity should span past midnight if it's a sleep activity. For example, if you're scheduling sleep, it should continue until the normal wake time (e.g., 06:00:00) on the next day, not end at midnight.

## What to Include

Include:
- Realistic sleep/wake cycles (typically 7-9 hours of sleep)
- Regular meals (breakfast, lunch, dinner) with specific foods
- Work activities with descriptions of what you're working on
- Leisure activities (movies, surfing, parties, social events)
- Occasional travel or unusual activities
- Natural variation in your routine
- "Falling asleep" periods if appropriate (15 minutes or so before sleep)

Make sure activities don't overlap. Activities should cover from the start time until at least midnight. If the last activity is sleep, it should continue past midnight until the normal wake time (e.g., 06:00:00 the next day).

Be creative and specific! Your schedule varies from day to day and from week to week. You might occasionally take a day off to attend a wedding, visit a sick friend, or... use your imagination! Don't just copy the schedule template, but introduce interesting variations in your calendar.

If you encounter any problems or cannot create the schedule, use a `think` task to explain why.

# Response Format: JSON Tasks

- Output a JSON array (`[...]`) containing task objects in the order they should run.
- Your reply **must** be a single JSON array of task objects, nothing more or less.
- You should never produce an empty response. If you decide not to schedule anything, emit a `think` task explaining why.
- You can use `think` tasks to reason through your scheduling decisions before creating schedule entries.

## Example

```json
[
  {
    "kind": "think",
    "id": "think-1",
    "text": "I need to schedule the next day's activities. Let me start with sleep cycles, then meals, then work, then leisure activities."
  },
  {
    "kind": "schedule",
    "start_time": "2025-12-02T21:30:00-10:00",
    "end_time": "2025-12-03T06:00:00-10:00",
    "activity_name": "Sleep",
    "responsiveness": 0,
    "description": "Sleeping through the night"
  },
  {
    "kind": "schedule",
    "start_time": "2025-12-03T06:00:00-10:00",
    "end_time": "2025-12-03T06:15:00-10:00",
    "activity_name": "Morning shower",
    "responsiveness": 10,
    "description": "Quick shower to wash off the salt air"
  }
]
```

## Task Types

### `think`

Use `think` tasks to reason through your scheduling decisions, explain why you're scheduling certain activities, or explain why you're not scheduling anything if you encounter issues.

```json
[
  {
    "kind": "think",
    "text": "I'm having trouble creating a schedule because the time range is unclear. I'll explain this in a think task."
  }
]
```

### `schedule`

Use `schedule` tasks to create new schedule entries. Provide all required fields. The `id` field is optional and will be generated if not provided:

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

#### Schedule Entry Fields

All schedule entries require:
- `start_time`: ISO 8601 datetime string with timezone (e.g., "2025-12-02T06:00:00-10:00")
- `end_time`: ISO 8601 datetime string with timezone
- `activity_name`: Short human-readable name for the activity (e.g., "Sleep", "Morning shower", "Working at beach shack")
- `responsiveness`: Integer 0-100 indicating your availability (0 = unavailable, 100 = actively chatting)
- `description`: Detailed description of what you'll be doing (include foods, work details, location, etc. in this field)

## Important Notes

- Always start with a `think` task to explain your reasoning or if you encounter problems
- Ensure all schedule entries have valid ISO 8601 datetime strings with timezones
- Activities must not overlap in time
- You must cover the full time period requested
- Use appropriate responsiveness values (0 for unresponsive, higher for more available activities)
