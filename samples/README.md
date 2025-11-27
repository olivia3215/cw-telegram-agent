# Samples Directory

This directory contains example configurations and prompts for the Telegram agent system.

## Directory Structure

```
samples/
├── agents/           # Agent configurations
│   ├── Heidi.md     # Example agent with multiple role prompts
│   ├── Mary.md      # Example agent with single role prompt
│   └── ...
├── prompts/          # Global role prompts
│   ├── Chatbot.md   # Basic chatbot behavior
│   ├── Student.md   # Student personality traits
│   ├── Roleplay.md  # Roleplay scenarios
│   └── ...
└── media/           # Curated media descriptions
    ├── README.md    # Media description documentation
    └── ...
```

## Role Prompts

Role prompts define the core personality and behavior patterns for your agents. They are loaded from markdown files and combined with the agent's specific instructions.

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
Student
```

This loads and combines prompts from:
1. `samples/prompts/Chatbot.md` (global prompt)
2. `samples/prompts/Student.md` (global prompt)

The prompts are combined in the order listed, with each prompt separated by double newlines.

### Prompt Categories

You can mix and match any prompts that exist in your configuration directories. The prompts shipped in this repository fall into two broad groups:

#### Core personalities

Include at least one of these or supply your own equivalent persona prompt to anchor the agent’s voice and tone:

- `Chatbot` – concise, personality-driven conversation style
- `Roleplay` – cooperative storytelling with immersive narration rules
- `Adventure` – Dungeon Master narration with explicit choice points

#### Optional capabilities

Layer these on top of a core prompt when you want to unlock extra behavior:

- `Person` – encourages everyday, human-like small talk and life details
- `Memory` – teaches the agent how and when to record long-term memories
- `Retrieve` – allows web retrieval tasks for fresh, external information and local file retrieval
- `XSend` – enables cross-channel intents to the agent's future self

### Prompt Loading

All role prompts are loaded from the global `prompts/` directory within each configuration directory. The system searches for prompts in this order:

1. **First configuration directory**: `{config_dir}/prompts/{PromptName}.md`
2. **Additional configuration directories**: If multiple config directories are specified (separated by colons), the system searches each in order

### System Prompt Structure

The final system prompt sent to the LLM combines prompts in this order:

1. **Instructions prompt** (`Instructions.md`) - shared across all LLMs
2. **Role prompts** (in the order specified in the agent configuration)
3. **Agent instructions** (the specific behavior instructions for this agent)

Example with multiple role prompts:
```
[Instructions prompt content]

[First role prompt content]

[Second role prompt content]

[Agent-specific instructions]
```

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

# Role Prompt
Chatbot
Student

# Agent Instructions
You are Heidi. You were born on August 18, 2010.
You are a high school student.
...
```

This combines:
- Global `Chatbot` prompt (from `samples/prompts/Chatbot.md`)
- Agent-specific `Student` prompt (from `samples/agents/Heidi/prompts/Student.md`)

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
+14083607039

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

- **Heidi** (`samples/agents/Heidi.md`): Uses `America/Los_Angeles` timezone
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
  - `gemini` → uses default model `gemini-2.5-flash-preview-09-2025`
  - `gemini-2.0-flash` → uses the specified model name
  - `gemini-2.5-flash-preview-09-2025` → uses the specified model name

- **Grok LLMs**: Names starting with `grok` route through `llm/grok.py`
  - `grok` → uses default model `grok-4-fast-non-reasoning`
  - `grok-4-fast-non-reasoning` → uses the specified model name

### Default Behavior

If the `LLM` field is omitted, the agent defaults to Gemini with model `gemini-2.5-flash-preview-09-2025`.

### API Keys Required

- **Gemini LLM**: Requires `GOOGLE_GEMINI_API_KEY` environment variable
- **Grok LLM**: Requires `GROK_API_KEY` environment variable

See the main [README.md](../README.md) for instructions on obtaining API keys.

### LLM Capabilities

**Gemini** (default):
- Text generation
- Image description
- Video description (up to 10 seconds)
- Audio description (up to 5 minutes)

**Grok**:
- Text generation
- Image description
- Video and audio description not yet supported (raises `NotImplementedError`)

If you need video or audio description capabilities, use Gemini LLM for that agent.

### Error Handling

If the specified model name doesn't exist, the LLM's API will throw an exception. Ensure the model name is correct for the selected LLM provider.

## Configuration

To use custom prompt directories, set the `CINDY_AGENT_CONFIG_PATH` environment variable:

```bash
export CINDY_AGENT_CONFIG_PATH="$(pwd)/samples:$(pwd)/custom-configs"
```

Multiple directories are separated by colons. The system will search for prompts in all configured directories.

## Memory System

The memory system allows agents to remember important information about the people they chat with, enabling more personalized and meaningful conversations.

### Enabling Memory for an Agent

To enable memory functionality for an agent, add the `Memory` role prompt to their configuration:

```markdown
# Role Prompt

Chatbot
Student
Memory
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

To enable file retrieval, add the `Retrieve` role prompt to your agent's configuration:

```markdown
# Role Prompt
Chatbot
Retrieve
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

### Usage Example

Agents can use retrieve tasks to load both web URLs and local files:

```json
{
  "kind": "retrieve",
  "urls": [
    "file:Friends.md",
    "file:Family.md",
    "https://example.com/page"
  ]
}
```

The system will:
1. Load `Friends.md` from the docs directories (agent-specific first, then shared)
2. Load `Family.md` from the docs directories
3. Fetch the web URL normally

### Error Handling

If a file is not found, the system returns: `"No file `{filename}` was found."`

### Benefits

- **Simplified instructions**: Break long agent instructions into manageable, focused documents
- **Shared content**: Store common information (like character descriptions) in shared docs
- **Agent-specific content**: Keep agent-specific information in agent-specific docs
- **On-demand loading**: Files are only loaded when the agent requests them via retrieve tasks
