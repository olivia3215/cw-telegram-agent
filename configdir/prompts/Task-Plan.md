<!-- SCHEMA_TASKS: plan, intend -->

# Planning Instructions

Intentions and plans help you coordinate longer-term goals across conversations and channels. Use them to capture commitments you make to yourself so you can follow through even after the current turn ends.

## Intentions (`intend`)

Intentions are global. They apply to you across every chat, so only record items that should influence your overall behavior.

- Capture goals, reminders, or commitments you want to keep in mind everywhere.
- Provide enough detail that your future self can act without re-reading the original chat.

### Adding or updating an intention

```json
[
  {
    "kind": "intend",
    "content": "Follow up with Wendy tomorrow morning about her rehearsal schedule."
  }
]
```

If you reuse an existing `id`, the previous intention is replaced:

```json
[
  {
    "kind": "intend",
    "id": "intent-1a2b3c4d",
    "content": "Check in with Diego on Saturday about how his recovery is going."
  }
]
```

### Removing an intention

Emit the same `id` with an empty `content` to delete it:

```json
[
  {
    "kind": "intend",
    "id": "intent-1a2b3c4d",
    "content": ""
  }
]
```

## Channel Plans (`plan`)

Plans are scoped to a specific channel (chat). Use them to coordinate multi-step actions, promises, or strategies that only make sense for that conversation. Plans appear in the order in which they were added.

### Adding or updating a plan

You can edit an existing plan by reusing its `id`:

### Removing a plan

Use an empty `content` with the plan's `id` to delete that plan:

## Best Practices

- Prefer intentions for cross-channel commitments; use plans for channel-specific tactics.
- Keep entries concise but actionable—future you should know exactly what to do.
- Delete obsolete items promptly so you do not follow outdated intentions or plans.
- When you complete a plan step, either delete it or replace it with the next action.
