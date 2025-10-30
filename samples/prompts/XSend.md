# XSend

This task lets you trigger action in another channel, carrying a text describing your intent.
The intent will be shown to you along with the conversation history in that channel to enable you to respond in a way that adapts to its local context in the the target conversation.

## Syntax

```markdown
# «xsend» <channel_id>

<intent body (optional)>
```

- <channel_id>: Numeric Telegram peer ID of the target conversation. Typically a user ID. The channel must not be the current channel.
- Intent body may be empty. If empty, no special system instruction is added.

When you use this task with a non-empty intent, the system prompt will include

```
# Cross-channel Trigger

*** IMPORTANT ***

You, {char}, triggered action on this channel autonomously. Your intent was:

<intent body>
```

Use this to coordinate across multiple ongoing conversations.
