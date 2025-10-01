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

## Configuration

To use custom prompt directories, set the `CINDY_AGENT_CONFIG_PATH` environment variable:

```bash
export CINDY_AGENT_CONFIG_PATH="$(pwd)/samples:$(pwd)/custom-configs"
```

Multiple directories are separated by colons. The system will search for prompts in all configured directories.
