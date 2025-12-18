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

Schedules are stored in the agent's state directory at `{STATE_DIRECTORY}/{agent_name}/schedule.json`. The schedule includes:
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

### Creating Curated Memories

You can manually create curated memories that are only remembered when the agent is in a direct chat with specific users by creating memory files in your agent's config directory. These memories are included along with automatically created ones and are perfect for important information you want to ensure the agent always remembers.

**Location**: `configdir/agents/AgentName/memory/UserID.json`

**Example**: See `samples/agents/Heidi/memory/6754281260.json` for a sample curated memory file.

**Format**: Provide a JSON array of memory objects. At minimum each object needs a `content` field; other properties are optional.

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

The agent will see these curated memories in addition to any automatically created global memories.

For detailed information about the memory system architecture, file formats, and implementation, see [DESIGN.md](../DESIGN.md).

### Global Curated Memories

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
3. Load the agent's schedule from `{STATE_DIRECTORY}/{agent_name}/schedule.json`
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
