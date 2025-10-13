# DESIGN

This document describes the high-level architecture of the Telegram agent, with specific attention to how we build prompts for Gemini and how message/Media context flows through the system.

## High-level data flow

1. **Inbound message (Telegram)** → `handlers/received.py`
2. **Media description injection** (stickers/photos/etc.) → `media_injector.py`
3. **Conversation assembly** → normalized `ChatMsg` records (one per original message), each with ordered `parts`
4. **Prompt build** → `build_gemini_contents(...)` (in `llm/prompt_builder.py`)
5. **Gemini call** → `GeminiLLM.query_structured(...)`
6. **Agent reply** → parse markdown task blocks → schedule tasks in the graph → send via Telegram

## Prompt structure (Gemini)

We never send a `system` role to Gemini. Instead:

- **System instruction** (persona/role prompt/model-specific notes/current time/chat type/curated stickers) is passed via the model’s **system_instruction** parameter.
- **Contents** contain only:
  - `user` turns — all non-agent speakers
  - `model` turns — the agent’s prior messages (we remap `assistant → model`)

This is required by newer Gemini families (e.g., `gemini-2.5-flash-preview-09-2025`) that reject `system` content and only accept `user`/`model` roles.

### History ordering and target message

- History is chronological (oldest → newest), capped by `history_size` (default 500 messages).
- The **target message** (the one we want a response to)...
  - Causes a system instruction to be added: "Consider responding to message with message_id NNNN."
  - In DMs, the target is the last message.
  - In groups, the target may be an earlier message (e.g., a reply to something above).

### Parts model (per message)

Each message is represented as ordered **parts**:

- `{"kind": "text", "text": "..."}`
- `{"kind": "media", "media_kind": "<sticker|photo|gif|audio|...>", "rendered_text": "...", "unique_id": "..."}`
- Additional media kinds are allowed; unknown kinds are preserved and shown as placeholders in the prompt.

We deliberately **render** media to compact, semantic text (e.g., sticker set/name + short description). This keeps prompts small and keeps behavior fast/offline in tests.

### Speaker & trace metadata

For non-agent messages, we prepend a small header part:

## Task Graph Lifecycle

The system uses a task graph to manage agent actions and responses. Each conversation has its own task graph that contains a sequence of tasks to be executed.

### Replanning Semantics

When a new message arrives, the system **prepares for replanning** by:

1. **Deleting the existing task graph** for that conversation
2. **Creating a new `received` task** to process the new message
3. **Queuing the task** for processing in the next tick

The **LLM is called** when the `received` task is handled during the tick loop, not immediately when the message arrives. This design minimizes LLM calls when people send multiple messages in rapid succession, as only the most recent message triggers a new plan.

This ensures that the agent's planned actions remain relevant to the current conversation context while being efficient with LLM usage.

### Callout vs Regular Tasks

- **Callout tasks**: Messages that explicitly mention the agent (e.g., `@agent_name` in groups)
- **Regular tasks**: Messages that don't explicitly mention the agent

**Current behavior:**
- In **group chats**: Only callout tasks trigger replanning; background chatter is ignored
- In **direct messages**: All messages trigger replanning (effectively treating all as callouts)

**Rationale:** Callouts ensure the agent only responds when directly addressed, preventing it from being derailed by background conversation in groups.

### Task Dependencies and Failure Handling

Tasks can depend on other tasks using the `depends_on` field. When a task fails:

1. **Retry logic**: Failed tasks are retried up to 10 times with 10-second intervals
2. **Graph deletion**: If a task exceeds max retries, the entire graph is deleted
3. **Replanning**: The agent waits for the next message to create a new plan

**Why delete the entire graph?** Later tasks may depend on failed tasks, so it's better to start fresh rather than leave the conversation in an inconsistent state.

### Task Types and Special Handling

The system supports multiple task types, each with specific behavior:

**Standard Tasks** (added to task graph):
- `send` - Send a text message to the user
- `sticker` - Send a sticker to the user
- `wait` - Wait for a specified duration
- `block` - Block the user
- `unblock` - Unblock the user
- `shutdown` - Shut down the agent
- `clear-conversation` - Clear the conversation history

**Special Tasks** (processed immediately, not added to task graph):
- `remember` - Store information in the agent's memory (processed during parsing, written to disk)
- `think` - Allow the LLM to reason before producing output (discarded during parsing)
- `retrieve` - Fetch web pages for retrieval augmentation (triggers retrieval loop, see below)

**Think Task Rationale:**

The `think` task enables the LLM to reason before producing any output tokens. This is inspired by research showing that allowing models to think before responding can improve coherence and emotional appropriateness, even without specific training for this capability.

**Key characteristics:**
- The body of a think task is completely discarded - never stored, never shown to users
- Multiple think tasks can appear anywhere in the LLM response
- Think tasks allow the model to:
  - Plan the structure of the entire response rather than generating token-by-token
  - Consider emotional context before responding
  - Reason through complex situations step-by-step
  - Avoid errors by thinking through potential issues

The LLM is instructed on how to use think tasks via the `samples/prompts/Think.md` role prompt.

## Retrieval Augmentation Architecture

The retrieval augmentation system enables agents to fetch information from the internet, allowing them to provide up-to-date information and answer questions that require external knowledge.

### Overview

When an agent needs information from the web, it can use the `retrieve` task to fetch URLs. The system then:
1. Fetches the requested web pages
2. Injects the content as system messages in the conversation
3. Re-queries the LLM with the new context
4. Repeats until the LLM has enough information or reaches a limit

### Retrieve Task Processing

The `retrieve` task is processed specially during the LLM response loop:

**Task Format:**
```markdown
# «retrieve»

https://www.google.com/search?q=quantum+computing
https://en.wikipedia.org/wiki/Quantum_computing
```

**URL Limits:**
- Maximum 3 URLs per retrieve task
- URLs must start with `http://` or `https://`
- Non-URL lines are ignored

### Retrieval Loop

The retrieval loop is implemented in `handle_received()`:

```python
while True:
    # 1. Build system prompt (conditionally include Retrieve.md)
    # 2. Inject retrieved content as system messages at conversation start
    # 3. Query LLM with combined history
    # 4. Parse response for retrieve tasks
    # 5. If no retrieve tasks: exit loop and process other tasks
    # 6. If retrieve tasks: fetch URLs and continue loop
```

**Key Features:**
- Retrieved content appears as system messages before conversation history
- Format: `"Retrieved from {url}:\n\n{content}"`
- No sender name for system messages (empty string)
- Retrieved content is cumulative across rounds

### URL Fetching

The `_fetch_url()` function handles web requests:

**Features:**
- 10-second timeout
- Follows redirects (`allow_redirects=True`)
- Content-type validation (HTML only)
- 40k character truncation
- Comprehensive error handling

**Non-HTML Content:**
```
Content-Type: application/pdf - not fetched (non-HTML content)
```

**Error Handling:**
```html
<html><body><h1>Error: Request Timeout</h1>
<p>The request timed out after 10 seconds.</p></body></html>
```

### Loop Control

**Duplicate Detection:**
- Tracks all retrieved URLs in a set
- If all requested URLs already retrieved → suppress Retrieve.md and retry
- Prevents infinite loops from repeated requests

**Maximum Rounds:**
- Default: 8 rounds (configurable via `RETRIEVAL_MAX_ROUNDS`)
- After max rounds → suppress Retrieve.md
- Ensures eventual termination even with persistent retrieve tasks

**Retrieve.md Suppression:**
The `Retrieve.md` prompt is conditionally included:
- Included: First N rounds, agent has "Retrieve" in role_prompt_names
- Suppressed: After max rounds OR duplicate URL detection
- Prevents infinite retrieval loops

### Configuration

**Environment Variable:**
```bash
export RETRIEVAL_MAX_ROUNDS=8  # Default: 8
```

**Agent Configuration:**
Only agents with `Retrieve` in their role prompts can use retrieval:
```markdown
# Role Prompt
Chatbot
Retrieve
```

### Retrieve.md Prompt

The `Retrieve.md` prompt provides comprehensive instructions:

**Search Resources:**
- Google Search: `https://www.google.com/search?q=...`
- Wikipedia: `https://en.wikipedia.org/w/index.php?search=...`
- Google Scholar: `https://scholar.google.com/scholar?q=...`
- Google News: `https://news.google.com/` or `https://news.google.com/search?q=...`

**Search-Then-Retrieve Pattern:**
1. First retrieve a search results page
2. Examine the results in the retrieved content
3. Retrieve specific pages from those results

**Geographic Awareness:**
The prompt includes examples for location-specific searches (e.g., India news)

### Security Considerations

**Current Implementation:**
- No domain whitelisting/blacklisting (may be added later)
- No content sanitization (HTML preserved as-is)
- 3 URL limit per task prevents excessive requests
- 40k truncation limits memory usage

**Future Enhancements:**
- Domain filtering
- Content extraction/cleaning
- Caching to avoid re-fetching
- State persistence across tasks

### Integration with LLM Loop

The retrieval system integrates seamlessly with the existing LLM query loop:

1. **System Prompt:** Conditionally includes Retrieve.md
2. **History:** Prepends retrieval system messages before conversation
3. **Task Parsing:** Detects retrieve tasks and triggers loop
4. **Task Execution:** Only executes retrieve tasks during retrieval rounds
5. **Final Response:** Processes send/sticker/etc. tasks after retrieval complete

**Benefits:**
- No changes to existing task types
- Transparent to non-retrieval agents
- Self-contained loop with clear exit conditions
- Maintains conversation context throughout retrieval

## Media Description Architecture

The system enriches conversations by describing photos and stickers using AI. This is managed through a composable chain of description sources with clear precedence and budget control.

### MediaSource Abstraction

All media description sources implement the `MediaSource` interface:

```python
class MediaSource:
    async def get(self, unique_id, agent, doc, kind, ...) -> dict | None:
        """Return description or None if not found"""
```

### Core Source Types

1. **`DirectoryMediaSource`**: Reads JSON files from a directory
   - Single directory responsibility
   - Loads all files into memory at creation time (no TTL)
   - Used for curated descriptions and AI cache

2. **`CompositeMediaSource`**: Chains multiple sources
   - Checks sources in order
   - Returns first non-`None` result
   - Immutable (configured at creation)

3. **`BudgetExhaustedMediaSource`**: Budget management
   - Consumes budget if available (returns `None` to continue)
   - Returns fallback if budget exhausted
   - Limits processing per tick (downloads + LLM calls)

4. **`AIGeneratingMediaSource`**: LLM-based generation
   - Downloads media and calls LLM
   - Caches successful results to disk and in-memory
   - Caches unsupported formats (no repeated checks)
   - Always succeeds (never returns `None`)

5. **`NothingMediaSource`**: Always returns `None`
   - Used when directories don't exist
   - Simplifies agent caching logic

### Chain Structure

The system builds a single global prioritized chain shared by all agents:

```
CompositeMediaSource([
    # All global curated (all config dirs)
    DirectoryMediaSource(config_dir1/media),
    DirectoryMediaSource(config_dir2/media),

    # AI cache and generation
    DirectoryMediaSource(state/media),        # AI cache
    BudgetExhaustedMediaSource(),             # Budget gate
    AIGeneratingMediaSource()                 # Always succeeds
])
```

**Priority order**: Global curated > AI cache > AI generation
**Within each level**: Earlier config directories in `CINDY_AGENT_CONFIG_PATH` take precedence

### Directory Hierarchy

Curated descriptions (human-generated) are in **config directories**, NOT state:

For each config directory in `CINDY_AGENT_CONFIG_PATH`:
1. **Global curated**: `{config_dir}/media/` (if exists)

Then:
2. **AI cache** (state directory): `state/media/` (AI-generated descriptions, runtime state)

### Budget System

- **Per-tick budget**: Default 8 AI description attempts per tick (configurable via `MEDIA_DESC_BUDGET_PER_TICK`)
- **Cache hits**: Do not consume budget (early chain exit)
- **Budget reset**: Reset at the start of each tick
- **Budget scope**: Covers downloads AND LLM calls (limits total processing)

**Purpose:** Rate-limit resource usage to maintain agent responsiveness and control costs.

### Curated Descriptions

Manual descriptions are stored as JSON files in config directories:

```json
{
  "unique_id": "901422453274706125",
  "kind": "sticker",
  "sticker_set_name": "MrRibbit",
  "sticker_name": "💻",
  "description": "A cartoon frog with a grumpy expression..."
}
```

Curated descriptions override AI-generated ones and are checked first in the chain.

### Known Issues

- **AnimatedEmojies sticker set**: Causes repeated description attempts due to data fetch failures

## Sticker System Architecture

The sticker system supports multiple sticker sets per agent.

### Multi-Set Configuration

Agents can be configured with:
- **Sticker sets**: `Agent Sticker Sets` (list of set names)
- **Explicit stickers**: `Agent Stickers` (specific set::sticker combinations)

### Resolution Strategy

1. **Task-specified set**: Use the set specified in the sticker task
2. **Cache lookup**: Check multi-set cache by (set_name, sticker_name)
3. **Telegram fetch**: Fetch from Telegram if not cached

**Current system:**
- `stickers`: Agent's configured stickers `(sticker_set_name, sticker_name) -> document`
- `sticker_set_names`: List of full sets to include
- `explicit_stickers`: Specific set::sticker mappings to include

**Requirements:**
- Both set name and sticker name are required in sticker triggers
- All sticker triggers must specify the sticker set name

## Caching Strategy

The system uses multiple caches to minimize API calls and improve performance.

### Cache Types and TTLs

| Cache | TTL | Purpose | Invalidation |
|-------|-----|---------|--------------|
| Entity cache | 5 minutes | Telegram entities (users, chats) | On `PeerIdInvalidError` |
| Mute cache | 60 seconds | Mute status per peer | Automatic expiration |
| Blocklist cache | 60 seconds | Blocked users | Automatic expiration |
| Media description cache | Persistent | AI-generated descriptions | Manual cache clear |
| Sticker cache | Session | Sticker documents | Session restart |

**Rationale:** Different TTLs balance freshness with API call minimization. Shorter TTLs for frequently changing data, longer for stable data.

## Error Recovery

The system implements comprehensive error recovery to handle various failure scenarios.

### Retry Logic

- **Max retries**: 10 attempts per task
- **Retry interval**: 10 seconds between attempts
- **Retry task creation**: Failed tasks create a `wait` task before retrying
- **Graph deletion**: After max retries, entire graph is deleted

**Purpose:** Handle transient failures while preventing infinite retry loops.

### Failure Scenarios

1. **Telegram API errors**: Retry with exponential backoff
2. **LLM failures**: Retry the entire planning process
3. **Media fetch failures**: Retry description attempts
4. **Network issues**: Retry with standard intervals

## Concurrency Model

The system uses a combination of async/await and threading to coordinate between Telegram events and task execution.

### Architecture

- **Telegram event handlers**: Async, add `received` tasks to work queue
- **Tick loop**: Synchronous, processes one task per tick
- **Work queue**: Thread-safe with locks for concurrent access
- **Round-robin scheduling**: Ensures fairness across conversations

### Coordination

1. **Event handlers**: Only add `received` tasks; no other processing
2. **Tick loop**: Processes all task types sequentially
3. **Locking**: Work queue uses locks to prevent race conditions
4. **State persistence**: Work queue state is saved after each task

**Benefits:** Simple coordination model with clear separation of concerns between event handling and task execution.

## LLM Integration Details

The system integrates with Google Gemini using a structured approach that separates system instructions from conversation content.

### System Instruction Handling

**Current approach:**
- System instructions are built from scratch for each LLM request
- Passed via the `system_instruction` parameter (not in message contents)
- Includes: persona instructions, role prompt, model-specific notes, current time, chat type, curated stickers

**Rationale:** System instructions are not part of the Telegram conversation and should be kept separate from message content.

### Role Prompts Architecture

The system supports multiple role prompts that are combined to create complex agent personalities:

**Loading Process:**
1. **Agent-specific prompts** (highest priority): `samples/agents/{AgentName}/prompts/{PromptName}.md`
2. **Global prompts** (fallback): `samples/prompts/{PromptName}.md`

**Combination Order:**
1. LLM-specific prompt (e.g., `Gemini.md`)
2. Role prompts (in the order specified in agent configuration)
3. Agent instructions (specific behavior instructions)

**Implementation Details:**
- Role prompts are loaded via `prompt_loader.load_system_prompt()`
- Multiple prompts are combined with double newlines (`\n\n`)
- Agent-specific prompts override global prompts for the same name
- No caching is used - prompts are loaded fresh for each agent instance

**Example System Prompt Structure:**
```
[LLM-specific prompt content]

[First role prompt content]

[Second role prompt content]

[Agent-specific instructions]
```

### Role Mapping

- **Input**: `assistant` role (agent's prior messages)
- **Output**: `model` role (Gemini API requirement)
- **User messages**: Remain as `user` role

**Purpose:** Compatibility with newer Gemini model families that reject `system` roles and require `user`/`model` roles only.

### API Compatibility

The system is designed to work with both legacy and newer Gemini API versions:
- **Legacy**: Supports `system` roles in contents
- **Newer**: Requires `system_instruction` parameter and `user`/`model` roles only

**Migration path:** The structured approach ensures compatibility with both versions while preparing for future API changes.

## Script Management System

The project uses a shared library approach for service management scripts to eliminate code duplication and provide consistent behavior across all services.

### Architecture

**Directory Structure:**
```
cw-telegram-agent/
├── run.sh                    # Agent server wrapper
├── media_editor.sh           # Media editor wrapper
├── telegram_login.sh         # Telegram login wrapper
├── src/                      # Python source code
│   ├── run.py               # Main agent server
│   ├── media_editor.py      # Media editor web interface
│   ├── telegram_login.py    # Telegram login utility
│   └── [other modules]
└── scripts/                  # Service management scripts
    ├── lib.sh               # Shared library
    ├── run.sh               # Agent server management
    └── media_editor.sh      # Media editor management
```

### Shared Library (`scripts/lib.sh`)

The shared library provides common functionality for all service scripts:

**Core Functions:**
- **Logging**: Colored output with `log_info()`, `log_success()`, `log_warning()`, `log_error()`
- **Validation**: `check_venv()`, `check_script()`, `check_env()`, `check_running()`
- **Process Management**: `start_server()`, `stop_server()`, `restart_server()`
- **Utilities**: `rotate_logs()`, `clean_cache()`, `setup_environment()`
- **Status**: `show_status()`, `show_logs()`, `show_recent_logs()`

**Configuration Variables:**
- `VENV_PATH` - Virtual environment path
- `LOG_DIR` - Log directory path
- `ENV_FILE` - Environment file path
- `SERVICE_NAME` - Service display name

### Callback System

Service scripts define callback functions that the shared library calls:

**Required Callbacks:**
- `startup_command()` - The actual command to start the service
- `custom_help()` - Service-specific help text

**Optional Callbacks:**
- `post_startup_hook()` - Called after successful startup (e.g., URL display)

### Service Script Pattern

Each service script follows this pattern:

```bash
#!/bin/bash

# Common configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Service-specific configuration
MAIN_SCRIPT="$PROJECT_ROOT/src/service.py"
PID_FILE="$PROJECT_ROOT/tmp/service.pid"
LOG_BASE_NAME="service"
SERVICE_NAME="Service Name"

# Source the shared library
source "$SCRIPT_DIR/lib.sh"

# Set LOG_FILE after sourcing library
LOG_FILE="$LOG_DIR/service.log"

# Callback functions
startup_command() {
    # Service-specific startup logic
    python "$MAIN_SCRIPT" "$@" < /dev/null > "$LOG_FILE" 2>&1 &
}

post_startup_hook() {
    # Optional post-startup actions
    log_info "Service started successfully"
}

custom_help() {
    cat << EOF
Service Management Script
# ... service-specific help content
EOF
}

# Run main function
main "$@"
```

### Wrapper Scripts

Simple wrapper scripts in the project root provide easy access:

```bash
#!/bin/bash
# Wrapper script for Service
exec "$(dirname "$0")/scripts/service.sh" "$@"
```

### Benefits

**Code Consolidation:**
- Eliminates ~200+ lines of duplication across scripts
- Single source of truth for common functionality
- Consistent behavior across all services

**Maintainability:**
- Bug fixes and improvements in one place
- Easy to add new services with minimal code
- Clear separation between common and service-specific logic

**User Experience:**
- Consistent command interface across all services
- Automatic log rotation and process management
- Comprehensive help and status reporting
- Graceful error handling and recovery

### Adding New Services

To add a new service:

1. **Create service script** in `scripts/` directory
2. **Define callback functions** for service-specific logic
3. **Create wrapper script** in project root
4. **Test** using the standard commands (`start`, `stop`, `status`, etc.)

The shared library handles all common functionality automatically.

## Memory System Architecture

The memory system enables agents to remember important information about conversation partners, creating more personalized and context-aware interactions.

### Memory File Structure

Memory files are organized in two locations using **global user-centric memory**:

```
configdir/
├── agents/
│   └── AgentName/
│       └── memory/
│           └── UserID.md        # Curated memories (manually created)
└── ...

state/
└── memory/
    └── AgentName.md            # Global episodic memories (automatically created)
```

- **Config memories** (`configdir/agents/AgentName/memory/UserID.md`): Manually curated memories that can be created and edited by hand
- **State memories** (`state/AgentName/memory.md`): Global episodic memories automatically created from agent conversations

**Global Memory Design:**
- Curated memories that are visible during all conversations can be written into the character specification `configdir/agents/AgentName.md`.
- Curated memories that are visible only when chatting with a given user are in the the manually created memory files `configdir/agents/AgentName/memory/UserID.md` where UserID is the unique ID assigned by Telegram to the conversation partner.
- Memories produced by the agent are stored in `state/AgentName/memory.md` and are viible by the agent during all conversations.

### Remember Task Processing

The `remember` task is processed immediately during LLM response parsing and does not go through the task graph:

1. **Immediate processing**: `remember` tasks are handled in `parse_llm_reply_from_markdown()`
2. **File writing**: Content is appended to the state memory file with timestamp
3. **No task graph**: These tasks are not added to the task graph, avoiding delays
4. **Error handling**: File write failures are logged but don't block conversation

### Memory Integration in Prompts

Memory content is integrated into the system prompt in a specific position within the complete prompt structure:

1. **LLM-specific prompt** (e.g., `Gemini.md`)
2. **Role prompts** (in the order specified in the agent configuration)
3. **Agent instructions** (the specific behavior instructions for this agent)
4. **Stickers section** (available stickers for the agent to send)
5. **Memory content** (curated and global memories)
6. **Current Time** (timestamp of the conversation)
7. **Chat Type** (direct or group chat)
8. **Message history** (conversation messages)

**Memory Loading Logic:**
- For direct messages: Uses the channel ID as the user ID
- For group chats: Currently uses the channel ID (future enhancement to support multiple users)
- Memory files are loaded dynamically on each prompt construction
- No caching ensures fresh memory content is always included. You can edit the memory state.

### Memory Loading and Caching

- **Dynamic loading**: System prompts are built fresh each time when memory is involved
- **No caching**: Memory content is loaded from disk on each prompt construction
- **Performance**: Acceptable since memory files are small and reads are infrequent
- **Freshness**: Ensures new memories appear immediately in subsequent conversations

### Memory File Format

Memory files use markdown format with timestamps:

```markdown
# Memory from 2025-01-26 14:30:15 UTC

User mentioned they have a younger sister named Sarah who is studying abroad.

# Memory from 2025-01-26 15:45:22 UTC

User works as a software engineer and enjoys hiking on weekends.
```

### Config Directory Tracking

Agents track their source config directory to enable memory loading:

- **Single directory**: Each agent is associated with one config directory where its `.md` file was found
- **Registration**: Config directory is stored during agent registration in `register_agents.py`
- **Memory loading**: Used to locate curated memory files in the correct config directory

### Memory Guidelines for Agents

The Memory role prompt teaches agents what to remember and what to avoid:

**Should remember:**
- Personal details (name, age, family, pets, job, hobbies)
- Important events (birthdays, anniversaries, achievements)
- Preferences (food, music, activities, communication style)
- Shared experiences and conversations
- Goals and aspirations
- Challenges they're facing

**Should avoid:**
- Temporary information (what they ate for lunch today)
- Sensitive personal details they haven't explicitly shared
- Information that might be private or confidential
- Negative judgments or opinions about others
- Details that already appear in their memory

### Privacy and Security Considerations

- **Local storage**: Memory files are stored locally and not shared between agents
- **Per-user**: Each user has their own memory file identified by user ID, containing all memories about them
- **LLM visibility**: Memory content is included in the system prompt, making it visible to the LLM
- **Selective memory**: Agents are instructed to be selective about what they remember to respect privacy
- **Manual curation**: Config memories allow manual review and editing of important information
- **Global persistence**: Memories persist across all conversations with the same user, enabling better relationship building
