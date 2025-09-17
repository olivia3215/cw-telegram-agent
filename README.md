# Cindy’s World Telegram Agent (cw-telegram-agent)

[![CI](https://github.com/olivia3215/cw-telegram-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/olivia3215/cw-telegram-agent/actions/workflows/ci.yml)

> **Purpose**: Permit an LLM to act like a regular Telegram user (DMs & groups) by representing behavior as **task graphs** and executing them on a **tick loop** with durable on‑disk state.

This README is written for a future developer (and future ChatGPT) to quickly regain context: what’s here today, how it runs, and where to extend it—especially around media understanding.

---

## Quick start

### Requirements
- **Python 3.13+**
- `pip install -r requirements.txt`

### Environment
Set these before first run (choose your paths):
```bash
export CINDY_AGENT_STATE_DIR="./state"    # persistent work queue + sessions
export AGENT_DIR="./agents"               # agent definition .md files
export TELEGRAM_API_ID="<from my.telegram.org>"
export TELEGRAM_API_HASH="<from my.telegram.org>"
```

### Login once per agent
```bash
python telegram_login.py
```
You’ll be prompted for Telegram code (and 2FA, if enabled). Sessions are stored under the state dir.

### Run the agent loop
```bash
python run.py
```
The loop: connect, process unread messages, (LLM planned) build task graphs, and execute one task per tick.

### Tests
```bash
PYTHONPATH=. pytest
```

---

## Mental model

### Core ideas
- **Task Graphs**: nodes with dependencies; common types today include `received`, `send`, `wait`.
- **Tick loop**: at each tick, pick **one eligible task** across all active graphs (round‑robin / fair), execute it, and persist state.
- **Durable state**: the work queue (graphs, nodes, etc.) is flushed atomically to **Markdown files with embedded JSON** in `CINDY_AGENT_STATE_DIR`. This allows recovery on restart.

### Typical flow
1. **Startup**: load agent definitions from `AGENT_DIR`; resume state from `CINDY_AGENT_STATE_DIR`; connect agent sessions to Telegram.
2. **Inbound**: when new Telegram messages arrive, a minimal **`received`** node is inserted (no immediate analysis).
3. **Handling**: when a `received` node is later processed, the handler pulls a chunk of **conversation history**, formats it for the LLM, and (as features land) asks the LLM to produce a new task graph.
4. **Execution**: tasks such as `send` get dispatched to Telegram; `wait` tasks block until their prerequisites are satisfied.

> **Note**: Today, the LLM call path is still evolving; the scaffolding is present.

---

## Repository map (modules & what to look for)

> File responsibilities are intentionally concise; read the top of each file for specifics.

- **`run.py`** – Entry point. Starts the tick loop, connects agents, drains unread messages.
- **`telegram_login.py`** – Interactive login per agent (API ID/code/2FA). Creates session files in state dir.
- **`telegram_util.py`** – Utilities for Telegram I/O (send/receive helpers, formatting, IDs, etc.).
- **`agent.py`** – Agent‑level glue: persona/config, state, and how an agent participates in graphs.
- **`handle_received.py`** – Task handler for `received` nodes: assembles conversation history and kicks off downstream actions (LLM integration point).
- **`llm.py`** – Abstraction layer for LLM calls (provider selection, prompt build, response unwrap). Initially minimal; expand here.
- **`task_graph.py`** – Core types for graphs and nodes, dependencies, readiness, serialization.
- **`task_graph_helpers.py`** – Builder/utility functions to construct or transform graphs.
- **`tick.py`** – Tick scheduling logic (eligible task selection & fairness).
- **`prompt_loader.py` / `prompts/`** – Load and fill prompt templates (e.g., replacing `{{AGENT_NAME}}`).
- **`markdown_utils.py`** – Read/write Markdown with embedded JSON blocks; atomic flush.
- **`register_agents.py`** – Scans `AGENT_DIR` and registers available agents.
- **`telegram_echo_agent.py`** – Minimal example or diagnostic agent that echoes.
- **`agents/`** – Your agent persona files in Markdown.
- **`tests/`** – Pytest suite covering readiness, retries, and persistence (plus mocks and logging checks).
- **`README.md`** – Overview & setup.

---

## Agent personas (`AGENT_DIR`)
Each agent is a **Markdown** file containing top‑level headings like:

```
# Agent Name
Ivy

# Agent Phone
+11234567890

# Agent Sticker Set
MY CUTE STICKERS

# Agent Instructions
You are {{AGENT_NAME}}, …
```
- All fields are required.
- `{{AGENT_NAME}}` is auto‑substituted in prompts.
- Multiple files → multiple agents can run concurrently.

---

## Prompt formatting conventions (current practice)
- **User text** in the history is wrapped in **French quotes**: `« … »` to make it stand out from system glue.
- (Planned) **Generated media descriptions** (see below) will be wrapped in **single angle quotes**: `‹ … ›`.

---

## Operational notes
- **Single‑threaded task execution**: one task per tick; inserting a `received` node is the only async bit and uses a lock.
- **Retries**: task execution errors bubble up; the scheduler retries (e.g., up to 10 times) depending on node policy.
- **Fairness**: the scheduler rotates across active conversation graphs to avoid starvation.

---

## Where to extend next: media understanding (design stub)
> **Status**: not implemented yet; this section records the intended design so we can add it consistently.

Goal: When a message (DM or group) contains images or stickers, replace them **in the LLM prompt** with rich **text descriptions** and cache those descriptions so repeated history windows don’t reprocess the same media.

### Requirements summarized
- Trigger description **lazily** when we are assembling the prompt for an LLM response (not at receipt time).
- Cache **on disk** under the state dir (one file per media item named by a stable `file_unique_id`). Also keep a short‑lived **in‑memory TTL cache**.
- **Share** the cache across all agents.
- Preserve **raw media files** temporarily under `state/photos/` for debugging (remove later when stable).
- **Photos / PNG / GIF / stickers** use the **same mechanism**. Stickers also record **sticker set name** and **sticker name**.
- Unsupported/huge media (e.g., videos) are represented as `'[kind] not understood'` and are **not stored**.
- **Quoting**: user text uses `«…»`; media descriptions use `‹…›`; sticker mention syntax example:

  `the sticker '😀' from the sticker set 'WENDYAI' that appears as ‹a picture of a woman …›`

### Integration points
1. **Telegram receive path**: keep it minimal—still just insert a `received` node.
2. **`handle_received.py`**: when building the history chunk for the prompt, detect media entries. For each media item:
   - Obtain Telegram’s `file_unique_id` (or equivalent stable ID).
   - If missing from cache → download media (and save under `state/photos/` while debugging), call the **vision‑capable LLM** (Gemini preferred; ChatGPT fallback) with a fixed “rich scene description” prompt, and write the result to `state/<unique_id>.txt`.
   - If present in cache → read it from disk (and also serve from the in‑memory TTL cache).
   - Replace the media element with the formatted description (and sticker metadata, when applicable).
3. **Error handling**: let exceptions propagate so the scheduler’s standard retry policy applies.

### Minimal prompt for description (example)
> “You are given a single image. **Describe the scene in rich detail** so a reader can understand it without seeing the image. Include salient objects, colors, relations, actions, and setting. Output **only the description**—no preface or metadata.”

Keep this prompt fixed for determinism; adjust later if lengths become an issue.

---

## Troubleshooting
- **Login loops**: delete the stale session for that agent in the state dir and re‑run `telegram_login.py`.
- **State corruption**: since state is Markdown+JSON, ensure atomic writes; if a file looks truncated, stop the process, fix/restore, then restart.
- **Rate limits**: if Telegram/LLM providers rate limit, the retry logic and single‑task tick model naturally back off.

---

## Glossary
- **Task Graph**: A DAG of actions (`received`, `send`, `wait`, …) for one conversation.
- **Tick**: One scheduling cycle that executes at most one ready node across all active graphs.
- **Eligible Task**: A node whose dependencies are satisfied and that is runnable now.

---

## Roadmap (near‑term)
- [ ] Implement media detection + `file_unique_id` extraction in the history builder.
- [ ] Add Gemini/ChatGPT vision call in `llm.py` with a `describe_image()` helper.
- [ ] On‑disk per‑media cache (`state/<unique_id>.txt`) + optional TTL memory cache.
- [ ] Sticker support (set name + sticker name + description) with `‹…›` quoting.
- [ ] Logging for cache hits/misses and new generations.
- [ ] Tests: unit tests for cache, prompt assembly, and “unsupported media” fallbacks.

---

## License
Private/experimental; license TBD.

## Media handling (photos, stickers, GIFs)

This repo supports enriching the LLM prompt with **descriptions of media** from Telegram chats. The feature is implemented so it’s robust, shareable across agents, and safe-by-default.

### Overview
- **Where it runs:** history is fetched in `insert_received_task_for_conversation()` (`task_graph_helpers.py`).
- **Hook:** the fetched messages pass through `inject_media_descriptions()` (`media_injector.py`).
  - By default this is **feature-flagged** via `MEDIA_FEATURE_ENABLED` in `media_injector.py`.
- **Download:** on cache miss (flag on), media bytes are downloaded once using `telegram_download.download_media_bytes()`.
- **Describe:** for **raster images** (jpeg/png/webp/gif), we call the agent’s provider: `agent.llm.describe_image(bytes)`.
  - Implemented for `GeminiLLM`; other providers can add the same method later.
- **Cache:** descriptions are persisted per-**file_unique_id** in `state/media/<id>.json` via `MediaCache`.
  - In-memory TTL avoids disk churn; on-disk cache is shared across agents and survives restarts.
- **Prompt assembly:** when building history lines & current message:
  - User text is wrapped in **French quotes** `« … »`.
  - Media descriptions are wrapped in **single angle quotes** `‹ … ›` (outside the French quotes).
  - Stickers render as:
    `the sticker '<name>' from the sticker set '<set>' that appears as ‹…›`

### Paths & state
- The **single source of truth** for the state directory is `media_cache.get_state_dir()`.
  - Respects `$CINDY_AGENT_STATE_DIR`, falling back to `state/`.
- Debug copies of downloaded media go to `state/photos/` with byte-sniffed extensions:
  - PNG/JPEG/GIF/WEBP/MP4/WEBM/TGS recognized; unknown falls back to `.bin`.
  - Toggle with `MEDIA_DEBUG_SAVE` in `media_injector.py`.

### What’s cached
- **Hit:** If a description exists, we never re-download; we inject the cached text.
- **Miss (raster image):** Download → describe via `agent.llm.describe_image()` → write JSON:

```json
  { "description": "...", "kind": "photo|sticker|gif|png|animation", "sticker_set": "...?", "sticker_name": "...?" }
```

* **Unsupported formats (e.g., `.tgs`, `.webm`, `.mp4`):**
  We cache a synthetic text like: `"<kind> not understood (format <ext>)"` so we **don’t re-download** next time.

### Provider ownership

* Vision is **provider-owned**: the injector calls `agent.llm.describe_image(bytes)`.

  * `GeminiLLM.describe_image()` uses Gemini’s REST API; other LLM classes can add API-specific implementations later without changing call sites.

### Design notes / constraints

* **No re-ordering:** Media is injected in Telegram delivery order.
* **No blocking:** Injector logs on failures and returns the input messages unchanged.
* **Resilience:** If the on-disk cache is deleted, descriptions are regenerated on demand.

### Quick dev tips

* Flip the flag: set `MEDIA_FEATURE_ENABLED = True` in `media_injector.py` for local runs.
* Peek artifacts: `state/photos/` for raw bytes, `state/media/` for JSON descriptions.
* CI: tests do **not** require network/API keys; vision calls are only used at runtime.
