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
- The **target message** (the one we want a response to) is NOT appended as a separate turn.
  - Instead, a system instruction is added: "Consider responding to message with message_id NNNN."
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

## Media Processing Pipeline

The system enriches conversations by describing photos and stickers using AI. This process is managed through a budget system to control resource usage.

### Budget System

- **Per-tick budget**: Default 8 AI description attempts per tick (configurable via `MEDIA_DESC_BUDGET_PER_TICK`)
- **Cache hits**: Do not consume budget (descriptions are cached in memory and on disk)
- **Budget reset**: Currently reset in the received handler (should be moved to start of each tick)

**Purpose:** Rate-limit LLM usage to maintain agent responsiveness and control costs.

### Description Workflow

1. **Media detection**: Photos and stickers are identified in incoming messages
2. **Cache check**: Check if description already exists
3. **Budget check**: Ensure budget is available for new descriptions
4. **AI description**: Use Gemini to generate rich descriptions
5. **Cache storage**: Store descriptions for future use

### Known Issues

- **AnimatedEmojies sticker set**: Causes repeated description attempts due to data fetch failures
- **Budget reset timing**: Should be moved to start of each tick for proper per-tick budgeting

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
- `sticker_cache_by_set`: Multi-set cache `(sticker_set_name, sticker_name) -> document`
- `sticker_set_names`: List of available sets
- `explicit_stickers`: Specific set::sticker mappings

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
