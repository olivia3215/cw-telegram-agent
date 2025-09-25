# cw-telegram-agent

This README is written for a future developer (and future ChatGPT) to quickly regain context: what’s here today, how it runs, and where to extend it—especially around media understanding.

[![CI](https://github.com/olivia3215/cw-telegram-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/olivia3215/cw-telegram-agent/actions/workflows/ci.yml)

---

## Quick start

### Requirements
- **Python 3.13+**
- `pip install -r requirements.txt`

### Environment
- `CINDY_AGENT_STATE_DIR` — directory for persisted queues, media cache, etc.
- `AGENT_DIR` — directory containing agent persona markdown files (one per agent).
- `GOOGLE_GEMINI_API_KEY` — for image descriptions (Gemini Vision).
- Optional tuning:
  - `MEDIA_DESC_BUDGET_PER_TICK` — integer, number of AI description attempts per received task (default 8).

### Run the agent loop
```bash
python run.py
```

The loop: connect, process unread messages, plan with the LLM, and execute one task per tick.

### Tests

```bash
PYTHONPATH=. pytest -vv
```

---

## Mental model

### Core ideas

* **Task Graphs**: nodes with dependencies; common types today include `received`, `send`, `sticker`, `wait`, `shutdown`, `clear-conversation`.
* **Tick loop**: at each tick, pick **one eligible task** across all active graphs (round-robin / fair), execute it, and persist state.
* **Durable state**: the work queue (graphs, nodes, etc.) is flushed atomically to Markdown files with embedded JSON in `CINDY_AGENT_STATE_DIR`. This allows recovery on restart.

### Typical flow

1. **Startup**: load agent definitions from `AGENT_DIR`; resume state from `CINDY_AGENT_STATE_DIR`; connect agent sessions to Telegram.
2. **Inbound**: when new Telegram messages arrive, the system inserts a minimal **`received`** task (no history fetch or LLM work here).
3. **Handling (`received` task)**: the tick-loop handler fetches recent **history**, warms/uses the **media description cache** (bounded **per-tick AI budget**), formats history and the **specific newly received message**, then asks the LLM to produce a new task graph.
4. **Execution**: tasks such as `send` / `sticker` / `wait` are dispatched by dedicated handlers; failures retry per policy.

---

## Repository map (modules & what to look for)

* **`agent.py`** – Agent registry and runtime agent state (including sticker caches).
* **`handlers.received.py`** – Prompt assembly helpers used by the received-task handler; builds system/user messages and formats media/sticker lines from cache.
* **`tick.py`** – Task handlers for all task types (including the `received` handler that runs the media-description pass and then calls into prompt building).
* **`media_injector.py`** – Media description subsystem:

  * cache-first helpers,
  * per-tick AI budget,
  * history processing (newest→oldest iteration internally, but preserving chronological order for the prompt),
  * single-message and list-of-messages formatters (cache-only).
* **`telegram_media.py`** – Media detection helpers (photo/sticker/gif/animation) and unique ID extraction.
* **`telegram_download.py`** – Download helpers (async) for raw media bytes.
* **`llm.py`** – LLM provider adapter; implements `describe_image(bytes, mime_type)` for image/sticker descriptions.
* **`run.py`** – Startup utilities, including `ensure_sticker_cache`.
* **`register_agents.py`** – Persona markdown parsing and agent registration.
* **`tests/`** – Unit & integration tests, including media cache and budget tests.

---

## Agent personas (`AGENT_DIR`)

An agent is defined by a markdown file with fields:

```markdown
# Agent Name
Wendy

# Agent Phone
+15551234567

# Role Prompt
Chatbot

# Agent Instructions
...long-form instructions to the agent...

# Agent Sticker Set
MyCuteStickers   # optional; use “None” or omit to disable a primary set

# Agent Sticker Sets
WendyDancer
CINDYAI

# Agent Stickers
WendyDancer :: 😉
CINDYAI :: 😀
```

Notes:

* All fields are required **except** sticker-related fields.
* Multiple files → multiple agents can run concurrently.
* The optional **Agent Sticker Sets** and **Agent Stickers** allow listing additional sets and explicit stickers for prompt surfacing (descriptions are filled from cache when available).

---

## Media handling (photos, stickers, GIFs) — current behavior

The agent enriches the LLM prompt by describing images/stickers found in recent history. Descriptions are cached on disk and in memory; a **per-tick AI budget** limits how many new descriptions can be computed each tick.

### Where it runs

* Inside the **`received` task handler** (tick loop), right before building the prompt.
* We iterate recent history **newest → oldest internally** to prioritize fresh content, but the final prompt preserves chronological order (oldest → newest).

### Per-tick AI budget

* Controlled by `MEDIA_DESC_BUDGET_PER_TICK` (default **8**).
* **Only AI attempts** consume budget; cache hits do not.
* When budget is exhausted, items remain undescribed this tick (no writes). They may be picked up in future ticks.

### Timeouts and failures

* Each AI attempt has a **12s timeout**.
* Cache entry structure (conceptual):

  * `description`: string **or** `null` (no sentinel strings like “not understood” in new writes),
  * `status`: `"ok"`, `"not_understood"` (terminal negative), `"timeout"`, or `"error"`,
  * sticker metadata when applicable (`set_name`, `sticker_name`), and `kind`.
* Absent entry = never attempted; `"not_understood"` = terminal negative (don’t retry); `"timeout"/"error"` = transient (retry in later ticks; backoff policy can be added).

### Prompt assembly conventions

* User text is wrapped with **French quotes**: `« … »`.
* Media descriptions use **single angle quotes**: `‹ … ›`.
* Stickers render like:
  `the sticker '<name>' from the sticker set '<set>' that appears as ‹…›`

### Provider hook

* Descriptions call `agent._llm.describe_image(bytes, mime)`. Gemini is supported; other providers can add the same method signature.

---

## Sticker triggers (LLM → actions)

The LLM triggers stickers using **two-line blocks** (optionally replying to a message):

```markdown
# «sticker»
<SET SHORT NAME>
<STICKER NAME>
```

Reply form:

```markdown
# «sticker» <MESSAGE_ID>
<SET SHORT NAME>
<STICKER NAME>
```

During the transition window the parser also accepts the legacy one-line body (`STICKER NAME` only), in which case the handler uses the agent’s primary set if present. The new two-line form is preferred.

---

## Operational notes

* `ensure_sticker_cache` loads the agent’s own sticker set(s) at startup if configured. This is independent of media descriptions.
* The description pass happens **only** in the received-task handler; prompt building itself is **cache-only**.
* The system replaces stickers/photos in the prompt history with cached descriptions; if none yet, the line is shown without a description suffix and will fill in over time as the budget allows.
* The agent can send stickers from any set by providing `<SET SHORT NAME>` + `<STICKER NAME>` in the trigger; the sender does **not** need that sticker to be in the agent’s curated set.

---

## Troubleshooting

* Seeing repeated “Starting direct file download…” lines or the bot appears frozen:

  * Ensure the per-tick budget is set sensibly (`MEDIA_DESC_BUDGET_PER_TICK`).
  * Confirm description work only happens in the received-task handler.
  * Check logs for `HIT/MISS/TIMEOUT/ERROR` lines from the description helper.

* Primary sticker set is optional:

  * Omit the *Agent Sticker Set* field or set it to “None” to disable preloading.
  * The agent can still send stickers from other sets by specifying the set name in the trigger.

---

## Near-term roadmap

* Optional curated description store (read-only) layered above disk cache.
* Flip all legacy persisted “not understood” strings to `null` + `status` (with test updates).
* Fine-tune budget/timeout envs and add light metrics.
* Small test additions around received-task context building and single-message formatting.

---
