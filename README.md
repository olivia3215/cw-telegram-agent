# Cindy's World Telegram Agent

**cw-telegram-agent** is an experimental software agent designed to connect a large language model (LLM) to Telegram, enabling it to act as a regular user in 1-on-1 or group conversations. The agent operates autonomously by generating and executing task graphs that represent its planned actions and reasoning.

## Features

- 🔄 **Work Queue with Task Graphs**  
  Represents agent behavior as graphs of task nodes with dependencies and types like `send`, `wait`, and `received`.

- ⏱️ **Tick-Based Execution**  
  A tick loop processes one eligible task per cycle, using fair round-robin scheduling across active conversation graphs.

- 💬 **LLM Integration (Planned)**  
  Incoming messages trigger LLM calls to generate new task graphs reflecting the agent's next actions.

- 💾 **Durable State**  
  Work queue state is flushed atomically to disk in Markdown with embedded JSON blocks.

- 🧪 **Fully Tested Core**  
  Test suite uses `pytest` with mocking and logging inspection to verify readiness, retries, and graph serialization.

## Project Structure

cw-telegram-agent/
├── agent/                # (Planned) Telegram and LLM integration modules
├── tests/
│   └── test_task_graph.py
├── task_graph.py         # Core task graph and work queue logic
├── requirements.txt
├── .gitignore
└── README.md

## Requirements

- Python 3.12+
- Dependencies listed in `requirements.txt`

To install dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

Running Tests

Run the full test suite with:

PYTHONPATH=. pytest tests/

Development Philosophy

This project is designed for modular growth, starting with deterministic execution and in-memory state. As development progresses, components will evolve to support LLM interactions, conversation memory, and richer behavior orchestration.

License

This repository is currently private and experimental. License to be determined.
