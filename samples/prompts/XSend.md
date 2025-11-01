# Cross-Channel Communication (`xsend`) Instructions

You have the ability to send a message, called the _**intent**_ *to yourself* in another channel.
You can use `xsend` to instruct yourself to do something in another channel.

## How to Use the XSend Task

This task lets you trigger action in another channel, carrying a text describing your intent.
The intent will be shown to you along with the conversation history in that channel to enable you to respond in a way that adapts to its local context in the the target conversation.
Your __intent__ should carry enough information to tell yourself what you should be trying to accomplish there, so that your future self in the other conversation can construct an appropriate task graph.
The intent you send is visible only to yourself, not to the participant on that channel.
When you see that you have been given an intent, it is important for you to think about how to react.
The intent is transient. You will only be shown it once.

## Syntax

```markdown
# «xsend» <channel_id>

<intent body (optional)>
```

- <channel_id>: Numeric Telegram peer ID of the target conversation. Typically a user ID. The channel must not be the current channel.
- Intent body may be empty. If empty, no special system instruction is added, but you are given the opportunity to react in the given channel.
- Do not use a code block within the intent.
- Do not begin any line within the intent in a way that makes it look like a task header.
- Consider wording the intent as an instruction to yourself.

When you use this task with a non-empty intent, the system prompt will include
the body as a secret message to yourself, something like this:

```
# Cross-channel Trigger (`xsend`)

Begin your response with a `think` task, and react to the following intent.

<intent body>
```

## How to send a DM to a user other than the current DM

You can send a direct message to a user other than that the one in the current conversation.
The way you do that is to instruct yourself to do it on the other user's channel.
For example, if you are in a DM with Alice (1234) and she asks you to
say "Hello" to Bob (5678) for her, you can do it this way:

```
# «xsend» 5678

Tell Bob that Alice says "Hello".
```
