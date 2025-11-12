# DEVELOPER

This document explains how to work on the codebase, how the Gemini integration is structured, and how to run tests.

## Setup

- Python 3.13
- Create a venv and install deps.
- Put your secrets/env in `.env` and `source` it before running.

## Run & test

```bash
# Run the agent (typical)
source .env
./run.sh start

# Or run directly for development
source .env
PYTHONPATH=src python src/run.py

# Test
PYTHONPATH=src pytest -vv
```

We do not allow slow or networked tests. Media, clock, and Gemini calls are mocked or rendered to compact text.

### Debug Logging

To see debug-level log messages (including detailed typing detection logs), set the `CINDY_LOG_LEVEL` environment variable:

```bash
export CINDY_LOG_LEVEL=DEBUG
```

Supported log levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` (defaults to `INFO`).

## Script Management System

The project uses a shared library approach for service management scripts:

### Structure
- **`scripts/lib.sh`** - Shared library with common functions
- **`scripts/run.sh`** - Agent server management script
- **`scripts/media_editor.sh`** - Media editor management script
- **Root wrappers** - Simple wrapper scripts for easy access

### Development Workflow

```bash
# Start services for development
./run.sh start
./media_editor.sh start

# View logs
./run.sh logs
./media_editor.sh logs

# Stop services
./run.sh stop
./media_editor.sh stop

# Check status
./run.sh status
./media_editor.sh status
```

### Adding New Services

To add a new service script:

1. Create `scripts/new_service.sh` with:
   - Service-specific configuration variables
   - Callback functions (`startup_command`, `custom_help`, etc.)
   - Source the shared library: `source "$SCRIPT_DIR/lib.sh"`

2. Create a wrapper script in project root:
   ```bash
   #!/bin/bash
   exec "$(dirname "$0")/scripts/new_service.sh" "$@"
   ```

The shared library provides common functionality:
- Logging functions with colors
- Process management (start/stop/restart)
- Log rotation and cleanup
- Environment setup
- Status reporting

## State directory structure

The system uses a state directory (configured via `CINDY_AGENT_STATE_DIR`) with the following structure:

```
state/
├── media/              # Media cache (JSON descriptions + debug media files)
│   ├── <unique_id>.json     # AI-generated descriptions
│   └── <unique_id>.<ext>    # Debug media files (.webp, .tgs, etc.)
├── <agent_name>/       # Agent session directories
│   └── telegram.session
├── work_queue.json     # Task queue state
└── work_queue.json.bak
```

**Important:** Agent names cannot be `media` as this conflicts with the reserved media directory.

## Code style & tooling

* `black` formats on commit (pre-commit hook).
* `ruff` prefers built-in generics (`list`, `dict`) and fixes unused imports.
* If needed:

  ```bash
  ruff check --fix .
  ruff format .
  ```

## LLM integration (Gemini)

### Builder: `build_gemini_contents(...)`

Emits:

1. **Leading system turn** (persona, role prompt, model-specific notes, time, chat type, curated stickers, target message instruction)
2. **Chronological history** (user/model turns with ordered parts)

> Implementation detail: we **extract** the system text and pass it via `system_instruction`. We never send a `system` role in `contents`. The target message is no longer appended as a separate turn; instead, a system instruction is added to respond to the specific message.

### Call path: `GeminiLLM.query_structured(...)`

* Extracts system text from the leading system turn.
* Sends `system_instruction` via Gemini model config; **contents** contain only `user` and `model` turns.
* Remaps `assistant → model` to satisfy stricter Gemini families.
* Uses compact rendered media text in parts to keep prompts small.

### Roles

* **user**: all non-agent speakers (group chats may contain many).
* **model**: the agent’s prior turns (assistant remapped to model).

### Target message selection

* In DMs: last message.
* In groups: may be earlier; we add a system instruction "Consider responding to message with message_id NNNN" so the model focuses on it.

### Logging

* We log a concise summary of built contents and, when available, model candidate counts/finish reasons for diagnosis.

## Adding/changing models

* Update the model string in config/env.
* The structured path is compatible with both prior defaults and newer families like:

  ```
  gemini-2.5-flash-preview-09-2025
  ```
* If you see an empty reply, consult logs:

  ```
  gemini.contents built: turns=… (history=…, target=…)
  gemini.response: candidates=… finish_reason=…
  ```

## Tests you’ll care about

* `tests/test_llm_builder_parts.py` — core coverage for the structured builder.
* `tests/test_prompt_sticker_descriptions.py` — ensures sticker descriptions are included.
* Other tests cover media budget/cache, parsing, task graph behavior, and Telegram media detection.

## Contributing workflow

* Small, focused PRs.
* Tests first (or alongside changes).
* One-file fences when possible.
* Commit messages in plain English.
* Run `PYTHONPATH=src pytest -vv` before each commit.
