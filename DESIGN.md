# DESIGN

This document explains how the system is put together so a future developer (or future LLM assistant) can come up to speed quickly.

_Last updated: 2025-09._

---

## High-level flow

1. **Startup**
   - Load agent persona files from `AGENT_DIR`.
   - Restore persisted task graphs from `CINDY_AGENT_STATE_DIR`.
   - Connect Telegram clients and (optionally) preload the agent’s own sticker set(s).

2. **Inbound**
   - When a new Telegram message arrives, enqueue a minimal **`received`** task (no expensive work on the event callback).

3. **Tick loop**
   - Each tick, select one eligible task across graphs (round-robin fairness) and execute it.
   - **`received`** tasks:
     - Fetch recent chat history (chronological order preserved).
     - Run **media description pass** (stickers & photos) with a **per-tick AI budget** (cache-first, newest→oldest iteration internally).
     - Build the LLM prompt from:
       - System prompt (agent instructions + runtime context like time/chat type + curated sticker list).
       - Formatted **history** (with media descriptions substituted from cache).
       - **The specific newly received message** formatted via the same cache-only logic.
     - Call the LLM to produce a plan (tasks).
   - Other tasks (`send`, `sticker`, `wait`, `shutdown`, `clear-conversation`) run through dedicated handlers.

4. **Persistence**
   - Task graphs and node states are written atomically to Markdown files with embedded JSON in `CINDY_AGENT_STATE_DIR` for durability.

---

## Key modules

- **`handlers/`**
  - One file per task type, each registering via `@register_task_handler(...)`.
  - `received.py`: executes the description pass, builds prompt, queries LLM, enqueues planned tasks.
  - `sticker.py`: resolves sticker documents from `(set, name)` using agent caches or transient fetch, sends via Telethon.
  - `send.py`, `wait.py`, `shutdown.py`, `clear_conversation.py`: respective simple handlers.

- **`handle_received.py`**
  - Prompt assembly helpers (system prompt construction, formatting history lines, etc.).

- **`media_injector.py`**
  - Description pipeline:
    - **Budget**: `MEDIA_DESC_BUDGET_PER_TICK` (default 8) limits AI attempts per `received` handling. Cache hits don’t spend budget.
    - **Order**: iterates **newest→oldest** internally to prioritize fresh items; returns history in original chronological order.
    - **Cache precedence**:
      1. In-memory TTL cache (state hit)
      2. On-disk cache in `state/media` → mirrored to memory
      3. (Planned) curated store → mirrored to disk+memory
      4. Download + describe via LLM (or “not understood”) → write to disk+memory
    - Writes “status” (`ok`, `timeout`, `error`, `not_understood`) and `description` (string or `null`).

  - Helpers:
    - `inject_media_descriptions(messages, agent)` warms cache (no reordering on return).
    - `format_message_for_prompt(message, agent)` returns one prompt line (cache-only, no IO).
    - `build_prompt_lines_from_messages(messages, agent)` maps messages → lines.

- **`telegram_media.py`**
  - Detects photos/stickers/GIFs/animations and extracts stable `unique_id` for caching.

- **`telegram_download.py`**
  - Async download of media bytes for LLM description path.

- **`llm.py`**
  - Gemini integration:
    - Uses `gemini-2.5-flash-preview-09-2025`.
    - Hard-coded **BLOCK_NONE** `safetySettings` for harassment, hate, sexually explicit, dangerous content.
    - `query(system, user)` — text path via SDK with safety.
    - `describe_image(bytes, mime)` — REST path with safety.
    - Logs concise safety findings when present.

- **`register_agents.py`**
  - Parses agent persona markdown. Primary sticker set is **optional** (“None” or omitted).

- **`agent.py`**
  - Agent registry and runtime state.
  - Sticker caches:
    - `sticker_cache`: legacy name→doc (canonical set only)
    - `sticker_cache_by_set`: `(set_short, sticker_name) → doc`
  - Optional fields: `sticker_set_names`, `explicit_stickers`.

- **`run.py`**
  - Startup helpers, including `ensure_sticker_cache(agent, client)` (skips if no primary set).

---

## Stickers

### Trigger format (LLM → agent)

Two-line blocks (optionally replying to a message ID):

```markdown
# «sticker»
<SET SHORT NAME>
<STICKER NAME>
````

Reply form:

```markdown
# «sticker» <MESSAGE_ID>
<SET SHORT NAME>
<STICKER NAME>
```

Legacy (transition): a single body line (`<STICKER NAME>`) is still accepted; handler uses the agent’s canonical set if available.

### Prompt exposure

* Curated stickers from the agent’s configured sets appear as:

  ```
  - WendyDancer :: 😉 - ‹a chibi woman winking›
  ```

  or, if description unknown:

  ```
  - WendyDancer :: 😉
  ```
* We avoid adding **seen** but **uncurated** stickers to the curated prompt list; the agent may still send them by specifying `<SET>` + `<NAME>` explicitly.

---

## Prompt construction (current)

* **System**: agent instructions, optional sticker list (curated sets only), current time, chat type.
* **History**: chronological lines built from messages (media substituted with `‹description›` when available).
* **User message**: the specific newly received message formatted via `format_message_for_prompt`.

> **Upcoming (active branch):** Switch Gemini calls to structured **role-based contents** (`system`, `user`, optional `assistant`) rather than concatenated text. This should also help avoid empty responses on the new model.

---

## Failure handling

* Media description errors/timeout:

  * Marked in cache as `timeout` or `error` (transient). Budget isn’t consumed on cache hits.
* Sticker send:

  * If a `(set, name)` doc can’t be resolved, we fall back to sending the sticker **name** as plain text and log a note (no Telegram error to the chat).

---

## Operational notes

* Primary sticker set is optional; preloading is skipped when absent.
* Description work runs only inside the `received` handler; prompt formatting is cache-only.
* Large animated sets (e.g., AnimatedEmojies) won’t be fully preloaded; we resolve items on demand.
