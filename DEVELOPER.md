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
│   ├── telegram.session
│   ├── memory.json     # Global memories (filesystem backend only)
│   ├── schedule.json   # Agent schedule (filesystem backend only)
│   └── memory/         # Channel-specific data (filesystem backend only)
│       └── {channel_id}.json
├── translations.json   # Translation cache (filesystem backend only)
├── work_queue.json     # Task queue state
└── work_queue.json.bak
```

**Storage Backend:**
The system uses MySQL for storing agent data (memories, intentions, plans, summaries, schedules, translations, media_metadata, agent_activity). Media files, Telegram sessions, and work queue state always remain in the filesystem. See README.md for MySQL setup instructions.

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

All LLMs use the shared `Instructions.md` prompt (formerly `Gemini.md`) which contains task format instructions and response guidelines. This ensures consistent behavior across LLM providers. These shared prompts (including `Task-*.md`) are located in `configdir/prompts` and must be included on the configuration path. Agent-specific role prompts are located in `samples/prompts`.

At startup, the agent performs a check to ensure `Instructions.md` is available in one of the configuration directories. If not found, the agent will report the error and exit.

### Adding/changing models

**For existing LLMs:**
* Update the model string in agent configuration (`LLM` field)
* Gemini defaults to `gemini-3-flash-preview` if name is just `gemini`
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

Agents can override the default LLM model for specific channels (conversations) using the `llm_model` property stored in MySQL.

**Location:** Stored in MySQL `conversation_llm_overrides` table.

**Configuration:**

The `llm_model` value specifies which LLM model to use for that channel. Can be set via the admin console or programmatically.

**Supported values:**
- `"gemini"` or `"grok"` - Uses the default model from `GEMINI_MODEL` or `GROK_MODEL` environment variable
- Specific model names like `"gemini-2.0-flash"` or `"grok-4-fast-non-reasoning"`

**Behavior:**
- When processing `received` tasks for a channel, the system checks for `llm_model` in MySQL
- If present, creates an LLM instance with that model (overriding the agent's default LLM)
- If the specified model is invalid or unavailable, falls back to the agent's default LLM
- The override affects both message history fetching (uses channel LLM's `history_size`) and LLM queries

**Implementation:**
- `Agent.get_channel_llm_model()` reads the `llm_model` value from MySQL `conversation_llm_overrides` table
- `handlers/received.py` uses this to select the appropriate LLM for each channel
- Logs indicate when a channel-specific LLM is being used

**Example usage:**
1. Use the admin console to set the LLM model override for a specific channel
2. Or use `db.conversation_llm.set_conversation_llm()` programmatically
3. The next `received` task for that channel will use the specified model

## Agent Architecture

### Mixin-Based Design

The `Agent` class uses a mixin architecture to organize functionality into logical modules. This design separates concerns and makes the codebase more maintainable.

**Structure:**

The `Agent` class (in `src/agent.py`) inherits from four mixins:

```python
class Agent(
    AgentExecutionMixin,    # Task execution and scheduling
    AgentPromptMixin,       # Prompt building and role management
    AgentStorageMixin,      # Memory and data storage
    AgentTelegramMixin,     # Telegram client management
):
    ...
```

**Mixin Responsibilities:**

1. **`AgentExecutionMixin`** (`agent/execution.py`)
   - Task graph execution
   - Work queue management
   - Task scheduling and dependencies

2. **`AgentPromptMixin`** (`agent/prompts.py`)
   - System prompt building
   - Role prompt loading and management
   - Prompt template rendering

3. **`AgentStorageMixin`** (`agent/storage.py`)
   - Memory loading (global and channel-specific)
   - Plan and summary content loading
   - Intention content loading
   - Schedule loading
   - Delegates to storage backend (MySQL)

4. **`AgentTelegramMixin`** (`agent/telegram.py`)
   - Telegram client creation and management
   - Entity caching and resolution (with contacts fallback)
   - Channel name resolution
   - Automatic contact addition for new DM conversations

**Benefits:**

- **Separation of concerns**: Each mixin handles a specific domain
- **Testability**: Mixins can be tested independently
- **Maintainability**: Changes to one area don't affect others
- **Extensibility**: New functionality can be added via new mixins

**Adding New Mixin Functionality:**

To add new functionality to a mixin:

1. Identify which mixin the functionality belongs to
2. Add methods to the appropriate mixin class
3. Methods automatically become available on `Agent` instances
4. Update tests to cover the new functionality

## Handler Registration System

The system uses a decorator-based registration pattern for task handlers. This allows handlers to be defined in separate modules and automatically registered when imported.

### Task Handler Types

**Regular Task Handlers:**
- Execute as part of the task graph
- Can have dependencies and delays
- Signature: `async def handle_X(task: TaskNode, graph: TaskGraph, work_queue=None)`

**Immediate Task Handlers:**
- Execute immediately during task parsing (before graph scheduling)
- Used for tasks that need instant execution (e.g., `think`, `remember`)
- Signature: `async def handle_X(task: TaskNode, *, agent, channel_id: int) -> bool`

### Registration

Handlers are registered using decorators:

```python
from handlers.registry import register_task_handler

@register_task_handler("send")
async def handle_send(task: TaskNode, graph: TaskGraph, work_queue=None):
    # Handler implementation
    ...
```

For immediate tasks:

```python
from handlers.registry import register_immediate_task_handler

@register_immediate_task_handler("think")
async def handle_think(task: TaskNode, *, agent, channel_id: int) -> bool:
    # Immediate handler implementation
    return True
```

### Handler Discovery

Handlers are automatically discovered when their modules are imported. The `handlers/__init__.py` file imports all handler modules to ensure registration:

```python
from . import (
    send,      # Registers "send" handler
    received,  # Registers "received" handler
    sticker,   # Registers "sticker" handler
    ...
)
```

### Task Dispatch

Tasks are dispatched via `handlers.registry.dispatch_task()`:

```python
from handlers.registry import dispatch_task

# Dispatch a regular task
await dispatch_task(task.type, task, graph, work_queue)
```

Immediate tasks are dispatched via `dispatch_immediate_task()`:

```python
from handlers.registry import dispatch_immediate_task

# Dispatch an immediate task
result = await dispatch_immediate_task(task, agent=agent, channel_id=channel_id)
```

## Adding New Task Types

To add a new task type to the system:

### 1. Define the Task Handler

Create a handler function in the appropriate module (or create a new one):

```python
# handlers/my_task.py

from handlers.registry import register_task_handler
from task_graph import TaskNode, TaskGraph

@register_task_handler("my_task")
async def handle_my_task(task: TaskNode, graph: TaskGraph, work_queue=None):
    """
    Handle my_task type.
    
    Args:
        task: The task node containing parameters
        graph: The task graph for dependency management
        work_queue: Work queue (deprecated, kept for compatibility)
    """
    # Extract parameters
    param1 = task.params.get("param1")
    param2 = task.params.get("param2")
    
    # Get agent and channel from graph context
    agent_id = graph.context.get("agent_id")
    channel_id = graph.context.get("channel_id")
    agent = get_agent_for_id(agent_id)
    
    # Implement task logic
    # ...
    
    # Task completes automatically when function returns
```

### 2. Register the Handler Module

Add the import to `handlers/__init__.py`:

```python
from . import (
    # ... existing handlers
    my_task,  # Registers "my_task" handler
)
```

### 3. Update Task Parsing (if needed)

If the task needs special parsing logic, update `handlers/received_helpers/task_parsing.py`:

```python
def parse_my_task(task_dict: dict, ...) -> TaskNode:
    """Parse my_task from LLM response."""
    task = TaskNode(
        type="my_task",
        params={
            "param1": task_dict.get("param1"),
            "param2": task_dict.get("param2"),
        }
    )
    return task
```

### 4. Add Tests

Create tests in `tests/test_my_task.py`:

```python
@pytest.mark.asyncio
async def test_handle_my_task():
    # Test implementation
    ...
```

### 5. Update Documentation

- Add task type to `DESIGN.md` if it's a major feature
- Update `samples/README.md` if it affects agent configuration
- Add examples to relevant documentation

### Immediate Tasks

For tasks that need immediate execution (e.g., `think`, `remember`):

```python
from handlers.registry import register_immediate_task_handler

@register_immediate_task_handler("my_immediate_task")
async def handle_my_immediate_task(task: TaskNode, *, agent, channel_id: int) -> bool:
    """
    Handle immediate task execution.
    
    Returns:
        True if task was handled, False otherwise
    """
    # Immediate execution logic
    return True
```

Immediate tasks are executed during task parsing, before graph scheduling.

## Storage Backend Abstraction

The system uses a storage abstraction to support both filesystem and MySQL backends. The abstraction is implemented through the `AgentStorageMixin` and storage factory pattern.

### Architecture

**Storage Interface:**
- `AgentStorageMixin` provides the interface for storage operations
- `AgentStorageMySQL` implements MySQL-based storage
- `agent/storage_factory.py` creates the appropriate storage backend

**Current Implementation:**
- **MySQL**: Used for all agent data (memories, intentions, plans, summaries, schedules, notes, media metadata, agent activity, channel metadata)
- **Filesystem**: Used for:
  - Media files (always on disk)
  - Telegram session files
  - Work queue state

### Data Storage Locations

**MySQL Tables:**
- `memories` - Global and channel-specific memories
- `intentions` - Agent intentions
- `plans` - Channel-specific plans
- `summaries` - Conversation summaries
- `schedules` - Agent schedules
- `notes` - Notes (conversation-specific memories) stored in MySQL
- `media_metadata` - Media description metadata
- `agent_activity` - Agent activity logs
- `conversation_llm_overrides` - Channel-specific LLM model overrides

**Filesystem:**
- `state/media/` - Media cache files (JSON + media files)
- `state/{agent_name}/telegram.session` - Telegram session
- `state/work_queue.json` - Task queue state

### Storage Factory

The storage factory (`agent/storage_factory.py`) creates the appropriate storage backend:

```python
from agent.storage_factory import create_storage

storage = create_storage(
    agent_config_name="MyAgent",
    agent_telegram_id=12345,
    config_directory=Path("/path/to/config"),
    state_directory=Path("/path/to/state"),
)
```

**Requirements:**
- `agent_telegram_id` must be set (agent must be authenticated)
- MySQL configuration must be complete (checked at startup)

### Storage Operations

All storage operations go through `AgentStorageMixin`:

```python
# Memory operations
memory_content = agent._load_memory_content(channel_id)
intention_content = agent._load_intention_content()

# Plan and summary operations
plan_content = agent._load_plan_content(channel_id)
summary_content = await agent._load_summary_content(channel_id)

# Schedule operations
schedule = agent._load_schedule()
```

The mixin delegates to the underlying storage backend (`AgentStorageMySQL`), which handles MySQL queries.

### Migration Notes

**From Filesystem to MySQL:**
- All agent data (memories, plans, summaries, channel metadata) migrated to MySQL
- Media files remain on filesystem
- See `README.md` for MySQL setup instructions

## Tests you'll care about

* `tests/test_llm_builder_parts.py` — core coverage for the structured builder.
* `tests/test_prompt_sticker_descriptions.py` — ensures sticker descriptions are included.
* Other tests cover media budget/cache, parsing, task graph behavior, and Telegram media detection.

## Contributing workflow

* Small, focused PRs.
* Tests first (or alongside changes).
* One-file fences when possible.
* Commit messages in plain English.
* Run `PYTHONPATH=src pytest -vv` before each commit.
