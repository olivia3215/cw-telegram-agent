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

### Agent-Specific Role Prompts

You can create agent-specific role prompts that override global ones by creating a `prompts/` subdirectory within an agent's directory:

```
samples/agents/Heidi/
├── prompts/
│   └── Student.md    # Agent-specific Student prompt
└── Heidi.md
```

When an agent named "Heidi" loads the "Student" role prompt, it will use `samples/agents/Heidi/prompts/Student.md` instead of the global `samples/prompts/Student.md`.

### Prompt Loading Priority

The system searches for prompts in this order:

1. **Agent-specific prompts** (highest priority): `samples/agents/{AgentName}/prompts/{PromptName}.md`
2. **Global prompts** (fallback): `samples/prompts/{PromptName}.md`

### System Prompt Structure

The final system prompt sent to the LLM combines prompts in this order:

1. **LLM-specific prompt** (e.g., `Gemini.md`)
2. **Role prompts** (in the order specified in the agent configuration)
3. **Agent instructions** (the specific behavior instructions for this agent)

Example with multiple role prompts:
```
[LLM-specific prompt content]

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

**Location**: `configdir/agents/AgentName/memory/UserID.md`

**Example**: See `samples/agents/Heidi/memory/6754281260.md` for a sample curated memory file.

**Format**: Simply write the information you want the agent to remember:

```markdown
User works as a software engineer at Google.
They have a golden retriever named Max who is 3 years old.
Their birthday is March 15th and they love chocolate cake.
```

The agent will see these curated memories in addition to any automatically created global memories.

For detailed information about the memory system architecture, file formats, and implementation, see [DESIGN.md](../DESIGN.md).

### Global Curated Memories

Anything that you want your agent to remember during all conversations should be placed in the agent's instructions.
