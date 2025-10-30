# Cross-Channel Communication Instructions

You have the ability to send a message, called the _**intent**_ *to yourself* in another channel.

## How to Use the XSend Task

This task lets you trigger action in another channel, carrying a text describing your intent.
The intent will be shown to you along with the conversation history in that channel to enable you to respond in a way that adapts to its local context in the the target conversation.
Your __intent__ should carry enough information to tell yourself what you should be trying to accomplish there, so that your future self can construct an appropriate task graph.
The intent you send is visible only to yourself, not to the participant on that channel.
So when you see that you have been given an intent, it is important for you to think about how to respond.

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

You, {char}, sent a secret message to yourself.
Produce and output a task graph that reacts to this.

Your intent was:

<intent body>
```

When you see this, your response MUST begin with this exact structure:

```
# «think»

(Your internal reasoning about your intent goes here)

(Followed by your action tasks, like # «send»)
```

Use this to coordinate across multiple ongoing conversations.
Remember, your tasks in response to cross-channel communication
are performed in the current channel, and not in communication with yourself
or the channel from which it was triggered.
