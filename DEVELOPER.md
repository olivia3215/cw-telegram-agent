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
- **`scripts/run.sh`** - Agent server management script (includes admin console)
- **Root wrappers** - Simple wrapper scripts for easy access

### Development Workflow

```bash
# Start the agent server (includes admin console on port 5001)
./run.sh start

# View logs
./run.sh logs

# Stop the server
./run.sh stop

# Check status
./run.sh status
```

The admin console is automatically started with the agent server and is accessible at http://localhost:5001 (configurable via `CINDY_ADMIN_CONSOLE_PORT`).

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

**Important:** Agent display names and config file names (the filename without `.md`) cannot be `media` as this conflicts with the reserved media directory. Both display names and config file names must be unique across all config directories to prevent state directory conflicts.

## Code style & tooling

* `black` formats on commit (pre-commit hook).
* `ruff` prefers built-in generics (`list`, `dict`) and fixes unused imports.
* If needed:

  ```bash
  ruff check --fix .
  ruff format .
  ```

## LLM integration

The system supports multiple LLM providers (Gemini and Grok) with a unified interface. Each LLM implementation follows the same pattern while adapting to each provider's API requirements.

### LLM Routing

The `llm.factory.create_llm_from_name()` function routes LLM creation based on the name prefix:
- `gemini` prefix → `GeminiLLM` (uses Google Gemini API)
- `grok` prefix → `GrokLLM` (uses xAI Grok API via OpenAI-compatible interface)

Agents specify their LLM via the `LLM` field in their configuration file.

### Gemini LLM

**Builder: `_build_gemini_contents(...)`**

Located in `llm/gemini.py`. Emits:

1. **Chronological history** (user/model turns with ordered parts)
2. System instructions are passed separately via `system_instruction` parameter

> Implementation detail: we **never** send a `system` role in `contents`. The target message is not appended as a separate turn; instead, a system instruction is added to respond to the specific message.

**Call path: `GeminiLLM.query_structured(...)`**

* Builds contents using `_build_gemini_contents()` (in `llm/gemini.py`)
* Sends `system_instruction` via Gemini model config; **contents** contain only `user` and `model` turns.
* Remaps `assistant → model` to satisfy stricter Gemini families.
* Uses compact rendered media text in parts to keep prompts small.

**System Prompt Building:**

The complete system prompt is built in `handlers/received_helpers/prompt_builder.py` via `build_complete_system_prompt()`, which assembles all components including specific instructions, base system prompt, sticker list, memory content, current time, channel details, conversation summary, and repeated specific instructions.

**Roles:**
* **user**: all non-agent speakers (group chats may contain many).
* **model**: the agent's prior turns (assistant remapped to model).

**Logging:**
* Set `GEMINI_DEBUG_LOGGING=true` for comprehensive prompt/response logging.

### Grok LLM

**Builder: `_build_messages(...)`**

Emits:

1. **System message** (if provided)
2. **Chronological history** (user/assistant turns with combined text parts)

**Call path: `GrokLLM.query_structured(...)`**

* Builds messages using `_build_messages()`
* Sends messages in OpenAI-compatible format (system, user, assistant roles)
* Combines message parts into single content strings per message
* Response should be JSON array per Instructions.md prompt

**Roles:**
* **system**: system instructions
* **user**: all non-agent speakers
* **assistant**: the agent's prior turns

**Logging:**
* Set `GROK_DEBUG_LOGGING=true` for comprehensive prompt/response logging.

### Shared Prompt System

All LLMs use the shared `Instructions.md` prompt (formerly `Gemini.md`) which contains task format instructions and response guidelines. This ensures consistent behavior across LLM providers.

### Adding/changing models

**For existing LLMs:**
* Update the model string in agent configuration (`LLM` field)
* Gemini defaults to `gemini-2.5-flash-preview-09-2025` if name is just `gemini`
* Grok defaults to `grok-4-fast-non-reasoning` if name is just `grok`

**For new LLM providers:**
1. Create `llm/{provider}.py` implementing the `LLM` base class
2. Set `prompt_name = "Instructions"` to use shared prompt
3. Add routing logic in `llm.factory.create_llm_from_name()`
4. Export in `llm/__init__.py`

**Debugging:**
* If you see an empty reply, consult logs:
  - Gemini: `gemini.contents built: turns=… (history=…, target=…)`
  - Grok: `grok.messages: turns=… (history=…)`
* Enable debug logging for the respective LLM provider.

### Channel-Specific LLM Model Override

Agents can override the default LLM model for specific channels (conversations) using the `llm_model` property in channel memory files.

**Location:** `{statedir}/{agent_name}/memory/{channel_id}.json`

**Configuration:**

The `llm_model` property in the channel memory file specifies which LLM model to use for that channel:

```json
{
  "llm_model": "grok-4-0709",
  "plan": [
    {
      "id": "plan-example",
      "content": "...",
      "created": "2025-01-15T10:00:00-08:00"
    }
  ]
}
```

**Supported values:**
- `"gemini"` or `"grok"` - Uses the default model from `GEMINI_MODEL` or `GROK_MODEL` environment variable
- Specific model names like `"gemini-2.0-flash"` or `"grok-4-fast-non-reasoning"`

**Behavior:**
- When processing `received` tasks for a channel, the system checks for `llm_model` in the channel memory file
- If present, creates an LLM instance with that model (overriding the agent's default LLM)
- If the specified model is invalid or unavailable, falls back to the agent's default LLM
- The override affects both message history fetching (uses channel LLM's `history_size`) and LLM queries

**Implementation:**
- `Agent.get_channel_llm_model()` reads the `llm_model` property from the channel memory file
- `handlers/received.py` uses this to select the appropriate LLM for each channel
- Logs indicate when a channel-specific LLM is being used

**Example usage:**
1. Manually edit the channel memory file: `state/Olivia/memory/6754281260.json`
2. Add or update the `llm_model` property
3. The next `received` task for that channel will use the specified model

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
