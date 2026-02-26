# Samples Directory

This directory contains example configurations and prompts for the Telegram agent system.

## Directory Structure

```
samples/
├── agents/           # Agent configurations
│   ├── Heidi.md     # Example agent with multiple role prompts
│   ├── Mary.md      # Example agent with single role prompt
│   └── ...
├── prompts/          # Agent-specific role prompts
│   ├── Chatbot.md   # Basic chatbot behavior
│   ├── Person.md    # Human-like small talk
│   ├── Roleplay.md  # Roleplay scenarios
│   └── ...
└── media/           # Curated media descriptions
    ├── README.md    # Media description documentation
    └── ...

configdir/
└── prompts/          # Shared system prompts (required)
    ├── Instructions.md
    ├── Task-Memory.md
    ├── Task-Retrieve.md
    └── ...
```

## Role Prompts

Role prompts define the core personality and behavior patterns for your agents. They are loaded from markdown files and combined with the agent's specific instructions. Shared system prompts that provide core capabilities are stored in `configdir/prompts`, while personality-specific prompts are stored in `samples/prompts`.

### Single Role Prompt

For a simple agent with one role, specify a single prompt name:

```markdown
# Role Prompt
Chatbot
```

This loads the prompt from `samples/prompts/Chatbot.md`.

### Multiple Role Prompts

You can combine multiple role prompts to create more complex personalities:

```markdown
# Role Prompt
Chatbot
Person
```

This loads and combines prompts from:
1. `samples/prompts/Chatbot.md` (global prompt)
2. `samples/prompts/Person.md` (global prompt)

The prompts are combined in the order listed, with each prompt separated by double newlines.

### Prompt Categories

You can mix and match any prompts that exist in your configuration directories. The prompts shipped in this repository fall into two broad groups:

#### Core personalities

Include at least one of these or supply your own equivalent persona prompt to anchor the agent’s voice and tone (typically found in `samples/prompts`):

- `Chatbot` – concise, personality-driven conversation style
- `Roleplay` – cooperative storytelling with immersive narration rules
- `Adventure` – Dungeon Master narration with explicit choice points
- `Person` – encourages everyday, human-like small talk and life details

#### Optional capabilities

Layer these on top of a core prompt when you want to unlock extra behavior (typically found in `configdir/prompts`):

- `Task-Memory` – teaches the agent how and when to record long-term memories
- `Task-Retrieve` – allows web retrieval tasks for fresh, external information and local file retrieval
- `Task-XSend` – enables cross-channel intents to the agent's future self
- `Task-Plan` – enables channel-specific planning capabilities
- `Task-Summarize` – enables conversation summarization capabilities

### Prompt Loading

All role prompts are loaded from the global `prompts/` directory within each configuration directory. The system searches for prompts in this order:

1. **First configuration directory**: `{config_dir}/prompts/{PromptName}.md`
2. **Additional configuration directories**: If multiple config directories are specified (separated by colons), the system searches each in order

**Note:** The system defaults to searching `samples:configdir` for these prompts if `CINDY_AGENT_CONFIG_PATH` is not set. This ensures that both sample agents and the required shared prompts like `Instructions.md` are available by default.

### System Prompt Structure

The final system prompt sent to the LLM combines prompts in this order:

1. **Specific instructions** - Context-specific instructions for the current turn (e.g., "New Conversation", "Target Message", "Cross-channel Trigger")
2. **Intentions section** - Channel Plan (if any) and Intentions (if any)
3. **Instructions prompt** (`Instructions.md`) - Shared across all LLMs (loaded from `configdir/prompts`)
4. **Agent instructions** - The specific behavior instructions for this agent
5. **Role prompts** - All role prompts in the order specified in the agent configuration (loaded from `samples/prompts` or other config directories)

Example with multiple role prompts:
```
[Specific instructions for this turn]

[Channel Plan (if any)]
[Intentions (if any)]

[Instructions.md content]

[Agent-specific instructions]

[First role prompt content]

[Second role prompt content]
```

Note: Additional sections are added later in the prompt building process (stickers, memory, current time, current activity, channel details, conversation summary).

### Creating Role Prompts

Role prompts are simple markdown files that describe personality traits, behavior patterns, or specific roles. Here are some examples:

**`samples/prompts/Chatbot.md`:**
```markdown
You are a helpful and friendly chatbot. You respond to questions clearly and concisely, and you're always polite and professional.
```

**`samples/prompts/Student.md`:**
```markdown
You are a high school student. You're curious about the world, ask lots of questions, and sometimes use informal language. You're still learning and growing.
```

**`samples/prompts/Roleplay.md`:**
```markdown
You enjoy roleplay scenarios and can take on different characters and situations. You're creative and imaginative in your responses.
```

### Examples

#### Heidi Agent (Multiple Role Prompts)

The `samples/agents/Heidi.md` file demonstrates multiple role prompts:

```markdown
# Agent Name
Heidi

# Agent Phone
+15551234567

# Agent Timezone
America/Los_Angeles

# Role Prompt
Chatbot
Person
Task-Memory
Task-Retrieve
Task-XSend

# Agent Instructions
I'm Heidi. I was born August 18, 2010. I'm a high-school student...
...
```

This combines:
- Global `Chatbot` prompt (from `samples/prompts/Chatbot.md`)
- Global `Person` prompt (from `samples/prompts/Person.md`)
- Global `Task-Memory` prompt (from `configdir/prompts/Task-Memory.md`)
- Global `Task-Retrieve` prompt (from `configdir/prompts/Task-Retrieve.md`)
- Global `Task-XSend` prompt (from `configdir/prompts/Task-XSend.md`)

#### Mary Agent (Single Role Prompt)

The `samples/agents/Mary.md` file shows a single role prompt:

```markdown
# Role Prompt
Roleplay
```

This loads only the `Roleplay` prompt from `samples/prompts/Roleplay.md`.

## Agent Timezone

Each agent can specify a timezone that determines how time is displayed and recorded throughout the conversation.

### Configuration

Add the `Agent Timezone` field to your agent's markdown file using [IANA timezone database](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) names:

```markdown
# Agent Name
Heidi

# Agent Phone
+15551234567

# Agent Timezone
America/Los_Angeles

# Role Prompt
Chatbot
Student

# Agent Instructions
You are Heidi...
```

**Common timezone examples:**
- `America/Los_Angeles` - US Pacific Time
- `America/New_York` - US Eastern Time
- `America/Chicago` - US Central Time
- `Pacific/Honolulu` - Hawaii Time
- `Europe/London` - UK Time
- `Asia/Tokyo` - Japan Time

### Default Behavior

If no timezone is specified, the agent uses the server's local timezone. Invalid timezone strings will fall back to the server timezone with a warning in the logs.

### How Timezone Affects the Agent

The agent's timezone is used in three important ways:

1. **Current Time Display**
   - The current time shown in the agent's system prompt uses the agent's timezone
   - Example: "The current time is: Wednesday January 15, 2025 at 02:30 PM PST"

2. **Message Timestamps in Conversation History**
   - Every message in the conversation history includes a timestamp in the metadata
   - These timestamps are converted to the agent's local timezone
   - The LLM sees these timestamps and can reason about timing in conversations
   - Example metadata: `[metadata] sender="Alice" sender_id=123456 message_id=789 time="2025-01-15 14:30:00 PST"`

3. **Memory Timestamps**
   - When the agent saves memories, they are timestamped in the agent's timezone
   - Example: `## Memory from 2025-01-15 14:30:00 PST conversation with Alice (123456)`

### Benefits

By using the agent's timezone:
- The agent experiences time from their perspective (e.g., a Hawaii-based agent knows when it's morning/evening in Hawaii)
- Temporal reasoning is more natural (the agent can understand "earlier today" or "last night" in their local context)
- Conversation timestamps help the agent understand the pacing and timing of discussions
- Memories are recorded in a way that's meaningful to the agent's location and daily rhythm

### Example Agents

- **Heidi** (`samples/agents/Heidi.md`): Uses `America/Los_Angeles` timezone, has multiple role prompts (Chatbot, Person, Task-Memory, Task-Retrieve, Task-XSend)
- See your agent configurations for more examples

## Agent LLM Configuration

Each agent can specify which LLM to use for generating responses. This allows you to use different models for different agents or experiment with different LLM providers.

### Configuration

Add the `LLM` field to your agent's markdown file:

```markdown
# Agent Name
Heidi

# Agent Phone
+14083607039

# LLM
gemini-2.0-flash

# Role Prompt
Chatbot
Student

# Agent Instructions
You are Heidi...
```

### LLM Routing

LLM names are routed based on their prefix:

- **Gemini LLMs**: Names starting with `gemini` route through `llm/gemini.py`
  - `gemini` → uses default model `gemini-3-flash-preview`
  - `gemini-2.0-flash` → uses the specified model name
  - `gemini-3-flash-preview` → uses the specified model name

- **Grok LLMs**: Names starting with `grok` route through `llm/grok.py`
  - `grok` → uses default model `grok-4-fast-non-reasoning`
  - `grok-4-fast-non-reasoning` → uses the specified model name

### Default Behavior

If the `LLM` field is omitted, the agent defaults to Gemini with model `gemini-3-flash-preview`.

### API Keys Required

- **Gemini LLM**: Requires `GOOGLE_GEMINI_API_KEY` environment variable
- **Grok LLM**: Requires `GROK_API_KEY` environment variable

See the main [README.md](../README.md) for instructions on obtaining API keys.

### LLM Capabilities

**Gemini** (default for agent responses):
- Text generation
- Image description (when used as MEDIA_MODEL)
- Video description (when used as MEDIA_MODEL, up to 10 seconds)
- Audio description (when used as MEDIA_MODEL, up to 5 minutes)

**Grok**:
- Text generation
- Image description (when used as MEDIA_MODEL)
- Video and audio description not yet supported (raises `NotImplementedError` when used as MEDIA_MODEL)

### Media Descriptions

Media descriptions (for images, videos, audio, and stickers) use a separate `MEDIA_MODEL` environment variable, not the agent's LLM configuration. This allows you to use different models for media descriptions versus agent responses.

**Configuration:**
Set the `MEDIA_MODEL` environment variable to specify which model to use for media descriptions:

```bash
export MEDIA_MODEL="gemini-2.0-flash"  # Use Gemini for media descriptions
# or
export MEDIA_MODEL="grok-4-fast-non-reasoning"  # Use Grok for media descriptions (images only)
```

**Important Notes:**
- Media descriptions are generated using `MEDIA_MODEL`, regardless of which LLM the agent uses for responses
- If you need video or audio description capabilities, set `MEDIA_MODEL` to a Gemini model (Grok doesn't support video/audio descriptions)
- The agent's `LLM` field only affects text generation for responses, not media descriptions

### Error Handling

If the specified model name doesn't exist, the LLM's API will throw an exception. Ensure the model name is correct for the selected LLM provider.

## Daily Schedules

Agents can have daily schedules that make them behave more human-like by having activities outside their Telegram conversations. When an agent has a schedule, they will have sleep cycles, meals, work, leisure activities, and other life events that affect their availability and responsiveness.

### Configuration

Add the `Daily Schedule` field to your agent's markdown file with freeform English text describing the agent's typical activities and preferences:

```markdown
# Agent Name
Heidi

# Agent Phone
+14083607039

# Agent Timezone
America/Los_Angeles

# Daily Schedule
I'm a night owl who typically wakes up around 10 AM. I work on coding projects 
in the afternoon, usually from 2 PM to 6 PM. I enjoy cooking dinner around 7 PM 
and often watch movies or play games in the evening. I go to bed around 2 AM. 
On weekends, I like to go surfing in the morning and meet friends for brunch.

# Role Prompt
Chatbot
Person

# Agent Instructions
You are Heidi...
```

### How It Works

When an agent has a daily schedule configured:

1. **Schedule Extension**: The schedule is automatically extended to 14 days from today whenever fewer than 7 days remain. The LLM generates realistic activities including sleep, meals, work, leisure, travel, and social events based on the agent's schedule description.

2. **Sleep Behavior**: When the agent is asleep (responsiveness 0), they don't participate in the tick loop and ignore incoming messages, reactions, and responses until they wake up.

3. **Responsiveness Delays**: The agent's responsiveness varies based on their current activity:
   - **Chatting** (responsiveness ~100): ~4 seconds delay before responding
   - **Working** (responsiveness ~30-50): 2-3 minutes delay before responding
   - **Asleep** (responsiveness 0): Delay until wake time
   - Other activities have delays interpolated between these values

4. **Current Activity Context**: The agent's current activity is included in the system prompt, allowing them to naturally mention what they're doing if asked. The agent can also retrieve their full schedule using `file://schedule.json` in a retrieve task.

5. **Schedule Retrieval**: Agents can access their schedule by using a retrieve task with the URL `file://schedule.json`. This allows them to "look at my calendar" when planning or responding to questions about availability.

### Schedule Storage

Schedules are stored in MySQL (see README.md for database setup). The schedule includes:
- Activities with start/end times (timezone-aware)
- Activity types (freeform strings like "sleeping", "eating", "working", etc.)
- Responsiveness levels (0-100)
- Descriptions and optional details (foods, work descriptions, locations, etc.)

### Activity Types

Activity types are freeform strings determined by the LLM when extending the schedule. Common examples include:
- `sleeping` - Agent is asleep (responsiveness: 0)
- `falling asleep` - Preparing for sleep (responsiveness: 10-20)
- `waking` - Just woke up (responsiveness: 30-40)
- `eating` - Having a meal (responsiveness: 40-60, includes `foods` array)
- `working` - Working on something (responsiveness: 30-50, includes `work_description`)
- `chatting` - Actively chatting on Telegram (responsiveness: 90-100)
- `leisure` - Other activities (responsiveness: 20-60, varies by activity)
- `traveling` - Traveling/commuting (responsiveness: 30-50)
- `social` - Social events, parties (responsiveness: 20-40)

The LLM decides appropriate activity types and responsiveness values based on the agent's schedule description.

### Special Behavior

- **xsend tasks**: Tasks triggered via `xsend` bypass all schedule delays and are processed immediately, even if the agent is asleep.
- **Read receipts**: Read receipts are delayed based on responsiveness, making the agent's behavior more realistic.
- **Schedule extension**: Happens automatically in the background during the tick loop, so it doesn't block message processing.

### Example

An agent with a schedule might respond like this:

**User**: "What are you doing?"

**Agent**: "I'm currently having breakfast (8:00 AM - 8:30 AM). I'm eating coffee, toast, and eggs. I'll be done in about 15 minutes, then I'll be working on my coding project."

The agent can also retrieve their full schedule to answer questions about future availability:

**User**: "Are you free tomorrow afternoon?"

**Agent**: [Uses retrieve task with `file://schedule.json`] "Let me check my schedule... I have a meeting from 2-3 PM, but I'm free after that!"

## Reset Context On First Message

Agents can be configured to automatically reset their conversation context (plans and summaries) when a new conversation begins. This is particularly useful for role-play or dungeon master agents where each new interaction should start from a clean state.

### Configuration

Add the `Reset Context On First Message` section to your agent's markdown file. The content of the section is ignored; its presence alone enables the behavior:

```markdown
# Agent Name
DungeonMaster

# Reset Context On First Message
(Any text here is ignored)

# Role Prompt
Adventure

# Agent Instructions
...
```

### How It Works

When this section is present in an agent's configuration:

1. **Start of Conversation Detection**: When the agent receives a message and the conversation history is empty (or only contains that one message), it's considered the "start of a conversation". This happens during the very first interaction or after a user has "Cleared History" in Telegram.
2. **Context Erasure**: At the start of a conversation, the agent automatically:
   - Erases all `plan`s for the conversation
   - Erases all conversation summaries
3. **Fresh Start**: The agent starts with a clean slate, unaffected by previous interactions or summaries from before the history was cleared.

### Manual Reset

This configuration also affects the `clear-conversation` task. If an agent with this setting processes a `clear-conversation` task (e.g., triggered by a command or an intention), it will also clear all plans and summaries for that conversation.

## Clear Summaries On First Message

Agents can be configured to automatically clear only their conversation summaries (not plans or notes) when a new conversation begins. This is useful for agents that maintain persistent state across scenarios using `note`s, but want to start each new scenario with a fresh conversation context.

### Configuration

Add the `Clear Summaries On First Message` section to your agent's markdown file. The content of the section is ignored; its presence alone enables the behavior:

```markdown
# Agent Name
Lucy

# Clear Summaries On First Message
(Any text here is ignored)

# Role Prompt
Chatbot
Task-Memory

# Agent Instructions
...
```

### How It Works

When this section is present in an agent's configuration:

1. **Start of Conversation Detection**: When the agent receives a message and the conversation history is empty (or only contains that one message), it's considered the "start of a conversation". This happens during the very first interaction or after a user has "Cleared History" in Telegram.
2. **Selective Context Erasure**: At the start of a conversation, the agent automatically:
   - Erases all conversation summaries
   - **Preserves** all `plan`s for the conversation
   - **Preserves** all `note`s for the conversation
3. **Partial Fresh Start**: The agent starts with fresh conversation context but retains tracking state (notes and plans) from previous scenarios.

### Differences from Reset Context On First Message

| Feature | Reset Context On First Message | Clear Summaries On First Message |
|---------|-------------------------------|----------------------------------|
| Clears summaries | ✓ | ✓ |
| Clears plans | ✓ | ✗ |
| Clears notes | ✓ | ✗ |
| Use case | Complete fresh start | Maintain state across scenarios |

### Use Case Example

An agent tracking multiple role-play scenarios uses `note`s to remember which scenarios have been completed, who the characters are, and other persistent state. When a user clears their Telegram history and starts a new scenario, the agent should:
- Forget the conversation details from the previous scenario (clear summaries)
- Remember the scenario tracking state (preserve notes)
- Keep any active plans (preserve plans)

This allows the agent to seamlessly move from one scenario to the next while maintaining continuity of the overall experience.

### Precedence When Both Flags Are Enabled

If both `Reset Context On First Message` and `Clear Summaries On First Message` are present in the agent configuration, **Reset Context On First Message takes precedence**. The agent will clear plans, summaries, and notes (complete reset) rather than just clearing summaries.

### Manual Reset

Like `Reset Context On First Message`, this configuration can also be triggered manually via the `clear-conversation` task. However, when using `clear-conversation`, the agent will only clear summaries and preserve plans and notes.

## Typing Behavior

Agents can be configured to simulate realistic typing behavior by specifying how long they wait before starting to type and how fast they type. This makes interactions feel more natural and human-like.

### Configuration

Add the `Start Typing Delay` and/or `Typing Speed` fields to your agent's markdown file:

```markdown
# Agent Name
Heidi

# Agent Phone
+14083607039

# Start Typing Delay
8.0

# Typing Speed
5.0

# Role Prompt
Chatbot

# Agent Instructions
You are Heidi...
```

### Start Typing Delay

The `Start Typing Delay` field specifies how many seconds the agent waits before starting to type a response. This simulates the time a human takes to read the message and formulate a response.

**Valid Range:** 1 to 3600 seconds (1 second to 1 hour)

**Default Behavior:** If not specified, the agent uses the global default from the `START_TYPING_DELAY` environment variable (defaults to 3.0 seconds if not set).

**Examples:**
- `1.0` - Very quick responses (1 second delay)
- `3.0` - Normal conversational pace (3 seconds delay)
- `8.0` - Thoughtful, slower responses (8 seconds delay)
- `60.0` - Very delayed responses (1 minute delay)

### Typing Speed

The `Typing Speed` field specifies how many characters per second the agent types. Combined with the message length, this determines how long the agent displays the "typing..." indicator.

**Valid Range:** 1 to 1000 characters per second

**Default Behavior:** If not specified, the agent uses the global default from the `TYPING_SPEED` environment variable (defaults to 20.0 characters per second if not set).

**Examples:**
- `5.0` - Very slow typing (realistic for a careful typist)
- `10.0` - Slow typing
- `20.0` - Normal typing speed (default)
- `50.0` - Fast typing
- `100.0` - Very fast typing

### How It Works

When an agent prepares to send a message:

1. **Calculate Delay**: `total_delay = start_typing_delay + (message_length / typing_speed)`
2. **Wait**: The agent waits for the calculated delay before sending the message
3. **Typing Indicator**: During this wait, Telegram shows the "typing..." indicator to conversation partners

**Example Calculation:**
- Start Typing Delay: `8.0` seconds
- Typing Speed: `5.0` characters/second
- Message: "Hello, how are you?" (19 characters)
- Total Delay: `8.0 + (19 / 5.0) = 8.0 + 3.8 = 11.8` seconds

### Benefits

By configuring typing behavior:
- Conversations feel more natural and human-like
- Different agents can have different typing personalities (thoughtful vs. quick, careful vs. rapid)
- Reduces the impression that responses are instantaneous/automated
- Allows conversation partners time to think about what they've said

### Example Agent

The `samples/agents/Heidi.md` configuration uses:
- `Start Typing Delay: 8.0` - Heidi takes time to think before responding
- `Typing Speed: 5.0` - Heidi types carefully and deliberately

## Stickers

Agents can be configured to use Telegram stickers in their responses. You specify sticker sets (full sets are loaded), and stickers in the agent's Saved Messages are also available.

### Configuration

Add the `Agent Sticker Sets` field to your agent's markdown file:

```markdown
# Agent Name
Heidi

# Agent Phone
+14083607039

# Agent Sticker Sets
UtyaDuck
HappyPenguin

# Role Prompt
Chatbot

# Agent Instructions
You are Heidi...
```

To curate specific stickers (e.g. from a large set), add those stickers to the agent's Saved Messages in Telegram; they are merged into the agent's sticker cache automatically.

### Agent Sticker Sets

The `Agent Sticker Sets` field specifies a list of Telegram sticker set names (one per line) that the agent can use. The agent can select appropriate stickers from these sets when responding.

**Format:** One sticker set name per line

```markdown
# Agent Sticker Sets
UtyaDuck
CindyPainter
WendyDancer
```

**How to Find Sticker Set Names:**
1. In Telegram, find a sticker you want to use
2. Tap the sticker to view the sticker pack
3. The sticker set name is usually visible in the pack details

### How Stickers Work

When an agent is configured with stickers:

1. **Selection**: The agent's LLM can choose to send a sticker as part of its response
2. **Context**: The agent sees available stickers in its system prompt and can reference them
3. **Usage**: Stickers appear as tasks in the agent's response (type: `sticker`)

Stickers come from:
- **Agent Sticker Sets**: Full sets listed in the config are loaded.
- **Saved Messages**: Any stickers in the agent's Saved Messages are also available (useful for curating a subset from large sets).

### Default Behavior

If no sticker sets are specified and there are no stickers in Saved Messages, the agent will not have access to stickers and cannot send them.

### Example Agent

The `samples/agents/Heidi.md` configuration uses:

```markdown
# Agent Sticker Sets
UtyaDuck
```

This gives Heidi access to all stickers in the UtyaDuck sticker set.

## Agent Control Fields

Agents have several control fields that affect their behavior and status in the system.

### Disabled

The `Disabled` section completely disables the agent, preventing it from processing any tasks or participating in conversations. The content of the section is ignored; its presence alone disables the agent.

**Configuration:**

```markdown
# Agent Name
Untitled Agent

# Agent Phone
+18005551212

# Agent Instructions
You are a helpful assistant.

# Disabled
```

**Behavior:**
- Disabled agents do not connect to Telegram
- Any existing task graphs for the agent are cancelled
- The agent does not participate in the tick loop
- The agent appears as disabled in the admin console

**Use Cases:**
- Temporarily disabling an agent without deleting its configuration
- Testing configurations without activating the agent
- Keeping example/template agents that should not be active

### Gagged

The `Gagged` section sets the agent's global default "gagged" status, which prevents the agent from automatically responding to messages. The content of the section is ignored; its presence alone gags the agent by default.

**Configuration:**

```markdown
# Agent Name
Heidi

# Agent Phone
+14083607039

# Agent Instructions
You are Heidi...

# Gagged
```

**Behavior:**
- Gagged agents do not create "received" tasks for incoming messages
- The agent will not automatically respond to conversations
- The agent can still process `xsend` tasks (which bypass gagged status)
- Per-conversation overrides can be set via the admin console to un-gag specific conversations

**Use Cases:**
- Agents that should only respond when explicitly triggered (e.g., via `xsend`)
- Testing agent configurations without having them respond to all messages
- Temporarily silencing an agent without fully disabling it

**Per-Conversation Overrides:**
The gagged status can be overridden on a per-conversation basis via the admin console:
- Set a conversation to "ungagged" even if the global default is gagged
- Set a conversation to "gagged" even if the global default is ungagged
- Remove the override to use the global default

**Difference from Disabled:**
- **Disabled:** Agent is completely inactive (no Telegram connection, no task processing)
- **Gagged:** Agent is active but doesn't automatically respond (can still process `xsend` and other internal tasks)

### Telegram ID

The `Telegram ID` field stores the agent's Telegram user ID. This field is typically auto-populated when the agent first connects to Telegram, but can also be manually specified.

**Configuration:**

```markdown
# Agent Name
Heidi

# Agent Phone
+14083607039

# Telegram ID
7359509635

# Agent Instructions
You are Heidi...
```

**Behavior:**
- **Auto-population:** When an agent first successfully connects to Telegram, the system automatically updates the configuration file with the agent's Telegram ID
- **Manual specification:** You can manually add this field if you already know the agent's Telegram ID
- **Persistence:** Once set, the Telegram ID is stored in the configuration file and used to identify the agent

**Use Cases:**
- **Database relationships:** The Telegram ID is used as a foreign key in database tables (notes, conversation parameters, etc.)
- **Agent identification:** Used to distinguish between different agents in the system
- **Admin console:** Used to manage agent-specific settings and relationships

**Important Notes:**
- This field is managed automatically by the system
- You typically don't need to manually set this field
- If the field exists and matches the connected agent's ID, no update is needed
- If the field is missing or incorrect, it will be automatically updated on connection

### Default Behavior

If none of these control fields are specified:
- The agent is **enabled** (not disabled)
- The agent is **ungagged** (responds to all messages)
- The **Telegram ID** will be auto-populated on first connection

## Configuration

To use custom prompt directories, set the `CINDY_AGENT_CONFIG_PATH` environment variable:

```bash
export CINDY_AGENT_CONFIG_PATH="$(pwd)/samples:$(pwd)/custom-configs"
```

Multiple directories are separated by colons. The system will search for prompts in all configured directories.

## Memory System

The memory system allows agents to remember important information about the people they chat with, enabling more personalized and meaningful conversations.

### Enabling Memory for an Agent

To enable memory functionality for an agent, add the `Task-Memory` role prompt to their configuration:

```markdown
# Role Prompt

Chatbot
Student
Task-Memory
```

This teaches the agent how to use the `remember` task to save information about conversation partners.

### How It Works

Agents automatically remember important details about people they chat with using **global user-centric memory**. When you tell an agent something important (like "I'm allergic to shellfish"), they can save this information and recall it in future conversations.

**Global Memory Benefits:**
- Memories persist across all conversations with all users
- Works seamlessly in both direct messages and group chats
- Each agent has a single memory file containing all memories
- Enables better relationship building and personalized interactions

### Creating Notes (Conversation-Specific Memories)

You can create notes that are only remembered when the agent is in a direct chat with specific users. These notes are conversation-specific memories stored in MySQL and can be managed via the admin console. They are included along with automatically created global memories and are perfect for important information you want to ensure the agent always remembers.

**Location**: Stored in MySQL `notes` table.

**Management**: Use the admin console to create, edit, and delete notes for specific users. The agent can also create and edit notes using the `note` task (see Task-Memory.md for details).

**Format**: Each memory object needs a `content` field; other properties (like `created`, `notes`) are optional.

```json
[
  {
    "kind": "memory",
    "created": "2025-02-01",
    "content": "User works as a software engineer at Google.",
    "notes": "Mentioned during intro call"
  },
  {
    "kind": "memory",
    "created": "2025-02-14",
    "content": "User has a golden retriever named Max who is 3 years old."
  },
  {
    "kind": "memory",
    "created": "2025-03-15",
    "content": "User's birthday is March 15th and they love chocolate cake."
  }
]
```

The agent will see these notes in addition to any automatically created global memories.

For detailed information about the memory system architecture, file formats, and implementation, see [DESIGN.md](../DESIGN.md).

### Global Memories

Anything that you want your agent to remember during all conversations should be placed in the agent's instructions.

## File Retrieval System

Agents with the `Retrieve` role prompt can load local documentation files using `file:` URLs in retrieve tasks. This enables you to simplify agent instructions by offloading content into separate documents that can be loaded on demand.

### Enabling File Retrieval

To enable file retrieval, add the `Task-Retrieve` role prompt to your agent's configuration:

```markdown
# Role Prompt
Chatbot
Task-Retrieve
```

### File Search Locations

When an agent requests a file using `file:filename`, the system searches in the following locations (in priority order):

1. **Agent-specific docs**: `{configdir}/agents/{AgentName}/docs/{filename}`
2. **Shared docs**: `{configdir}/docs/{filename}`

The system searches through all configuration directories (from `CINDY_AGENT_CONFIG_PATH`) in order, with earlier directories taking precedence.

### File Naming Rules

- Filenames must not contain the `/` character (prevents directory traversal)
- Filenames can have any extension (`.md`, `.txt`, etc.) or no extension
- Filename matching is case-sensitive

### Directory Structure

Organize your documentation files like this:

```
samples/
├── agents/
│   └── AgentName/
│       └── docs/
│           ├── Friends.md      # Agent-specific documentation
│           └── Family.md
└── docs/
    ├── Wendy.md                # Shared documentation
    └── Cindy.md
```

### Special File: schedule.json

Agents with daily schedules can retrieve their schedule using `file://schedule.json`. This returns the agent's current schedule in JSON format, allowing them to check their calendar and answer questions about availability.

### Usage Example

Agents can use retrieve tasks to load both web URLs and local files:

```json
{
  "kind": "retrieve",
  "urls": [
    "file:Friends.md",
    "file:Family.md",
    "file:schedule.json",
    "https://example.com/page"
  ]
}
```

The system will:
1. Load `Friends.md` from the docs directories (agent-specific first, then shared)
2. Load `Family.md` from the docs directories
3. Load the agent's schedule from MySQL (same data used by the schedule task)
4. Fetch the web URL normally

### Error Handling

If a file is not found, the system returns: `"No file `{filename}` was found."`

For `schedule.json`, if the agent doesn't have a daily schedule configured, the system returns: `"Agent does not have a daily schedule configured."`

### Benefits

- **Simplified instructions**: Break long agent instructions into manageable, focused documents
- **Shared content**: Store common information (like character descriptions) in shared docs
- **Agent-specific content**: Keep agent-specific information in agent-specific docs
- **On-demand loading**: Files are only loaded when the agent requests them via retrieve tasks
- **Schedule access**: Agents can check their own schedules to answer questions about availability
