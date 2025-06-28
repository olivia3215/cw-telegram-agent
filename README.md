# Cindy's World Telegram Agent

**cw-telegram-agent** is an experimental software agent designed to connect a large language model (LLM) to Telegram, enabling it to act as a regular user in 1-on-1 or group conversations. The agent operates autonomously by generating and executing task graphs that represent its planned actions and reasoning.

## Features

- **Work Queue with Task Graphs**  
  Represents agent behavior as graphs of task nodes with dependencies and types like `send`, `wait`, and `received`.

- **Tick-Based Execution**  
  A tick loop processes one eligible task per cycle, using fair round-robin scheduling across active conversation graphs.

- **LLM Integration (Planned)**  
  Incoming messages trigger LLM calls to generate new task graphs reflecting the agent's next actions.

- **Durable State**  
  Work queue state is flushed atomically to disk in Markdown with embedded JSON blocks.

- **Fully Tested Core**  
  Test suite uses `pytest` with mocking and logging inspection to verify readiness, retries, and graph serialization.

## Requirements

- Python 3.12+
- Dependencies listed in `requirements.txt`

To install dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Running Tests

Run the full test suite with:

```bash
PYTHONPATH=. pytest
```

## Agent Setup and Configuration

Before running the agent, you must configure at least one agent persona using a markdown file.

### Agent Markdown Files

Each agent is defined by a markdown file located in the directory specified by the `AGENT_DIR` environment variable. Each file should include the following fields as top-level markdown headings:

```markdown
# Agent Name

Ivy

# Agent Phone

+11234567890

# Agent Sticker Set

MY CUTE STICKERS

# Agent Instructions

You are {{AGENT_NAME}}, a cheerful AI who helps students prepare for their exams...
```

- All fields are required.
- `{{AGENT_NAME}}` will be substituted automatically during prompt construction.
- You may include multiple markdown files in the agent directory to support multiple simultaneous agents.

## Required Environment Variables

The following environment variables must be set before running the login or agent runtime:

```bash
export CINDY_AGENT_STATE_DIR="./state"       # where persistent work queue and session info is stored
export AGENT_DIR="./agents"                  # directory containing *.md files defining agent setup
export TELEGRAM_API_ID="<your Telegram API ID>"
export TELEGRAM_API_HASH="<your Telegram API HASH>"
```

> Both `CINDY_AGENT_STATE_DIR` and `AGENT_DIR` must point to valid, writable directories. Create them if needed.

---

## Logging in to Agents

You must log in each Telegram account before running the agent. This is done interactively via:

```bash
python telegram_login.py
```

This script will load the agent definitions from `$AGENT_DIR`, then walk through each account and prompt for the verification code (and 2FA password, if needed). You only need to do this once per device unless the session expires.

---

## Running the Agent

Once all agents are logged in, run the main server loop:

```bash
python run.py
```

The agent will:

- Connect to Telegram for each registered agent
- Process any unread messages
- Generate task graphs using the LLM
- Execute tasks on a tick loop

---

## Development Philosophy

This project is designed for modular growth, starting with deterministic execution and in-memory state. As development progresses, components will evolve to support LLM interactions, conversation memory, and richer behavior orchestration.

## License

This repository is currently private and experimental. License to be determined.
