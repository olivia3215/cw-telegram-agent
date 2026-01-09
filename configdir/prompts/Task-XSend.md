<!-- SCHEMA_TASKS: xsend -->

# Cross-Channel Communication (`xsend`) Instructions

You have the ability to send a message, called the _**intent**_ *to your future self* in another channel.
You can use `xsend` to instruct yourself to do something in another channel.

## How to Use the XSend Task

This task lets you trigger action in another channel, carrying a text describing your intent.
The intent is **an instruction you are sending to your future self**.
The intent will be shown to you along with the conversation history in that channel
to enable you to respond in a way that adapts to its local context in the the target conversation.
Your __intent__ should carry enough information to tell your future self what to do there,
so that your future self in the other conversation can construct appropriate tasks.
The intent you send is visible only to your future self in the other channel, not to the participant on that channel.
The intent is transient. You will only be shown it once.

## Syntax

```json
[
  {
    "kind": "xsend",
    "target_channel_id": <channel_id>,
    "intent": "<optional intent body>"
  }
]
```

- `target_channel_id`: Numeric Telegram peer ID of the target conversation (cannot be the current channel).
- `intent`: Optional string instruction you give to yourself. If omitted or empty, you'll simply be nudged to review that conversation.
- Phrase the intent as a directive to your future self.

When you use this task with a non-empty intent, the system prompt will include
the body as a secret message to your future self, something like this:

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

```json
[
  {
    "kind": "xsend",
    "target_channel_id": 5678,
    "intent": "Tell Bob that Alice says \"Hello\"."
  }
]
```

If you are relaying a message like this, **the intent body must explicitly include the intended recipient's identity**. The recipient of the xsend (your future self) will not know to whom an intended action is to be applied unless that is explicitly stated in the intent. Don't assume that, just because it is received on Bob's channel, that you will know that the message is intended to be relayed to Bob.

## How to react in a DM to a user other than the current DM

You can cause yourself to react in another channel to something you learned in this one.
The way you do that is to tell youself what you need to know on the other user's channel.
For example, if you are in a DM with Alice (1234) and she tells you something about Bob (5678),
you can do it this way:

```json
[
  {
    "kind": "xsend",
    "target_channel_id": 5678,
    "intent": "Alice told me that Bob won a Nobel Prize today for his work on high temperature superconductivity! Congratulate him."
  }
]
```
