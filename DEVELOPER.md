# DEVELOPER GUIDE

_Last updated: 2025-09._

This doc is for working on the codebase: local dev, tests, debugging, and common workflows.

---

## Local dev basics

### Python & deps
- Python 3.11+ recommended (tests currently run on 3.13 fine).
- Create a venv and install deps as usual.

### Environment you’ll need while developing
- `CINDY_AGENT_STATE_DIR` – writable dir for queues + media cache (e.g. `./state`).
- `AGENT_DIR` – folder of persona markdown files.
- `GOOGLE_GEMINI_API_KEY` – needed for media descriptions and LLM text.

### Run the server
```bash
python run.py
````

### Tests (run these **before each commit**)

```bash
PYTHONPATH=. pytest -vv
```

---

## Repository structure (dev view)

* `handlers/` — one task handler per file (registered via `@register_task_handler`).

  * `received.py`, `send.py`, `sticker.py`, `wait.py`, `shutdown.py`, `clear_conversation.py`
* `handle_received.py` — prompt assembly helpers (system/history formatting).
* `media_injector.py` — description pipeline (per-tick AI budget; cache-first).
* `telegram_media.py` — media detection + stable IDs.
* `telegram_download.py` — async media download helper.
* `llm.py` — Gemini integration (BLOCK\_NONE safety for SDK + REST).
* `agent.py` — registry and runtime agent state (sticker caches).
* `run.py` — startup helpers (including sticker cache preload).
* `register_agents.py` — parse persona markdown.
* `tests/` — unit + integration tests.

For an architectural overview see `DESIGN.md`.

---

## Coding patterns

### Task handlers

* Each handler lives in `handlers/<type>.py`.
* Decorate with `@register_task_handler("<type>")`.
* Handler signature:

  ```py
  async def handle_<type>(task: TaskNode, graph: TaskGraph) -> None: ...
  ```
* Pull contextual info from `graph.context` (e.g., `agent_id`, `channel_id`).

### Prompt construction (current)

* Done in the **received** handler using helpers from `handle_received.py`.
* Media descriptions are **cache-only** at prompt time; warming happens just before via `media_injector.inject_media_descriptions()`.

### Media description budget

* Env: `MEDIA_DESC_BUDGET_PER_TICK` (default 8).
* Budget is consumed only by **new AI attempts**; cache hits are free.
* Timeout per item ≈ 12s; transient failures recorded as `timeout`/`error`.

### Sticker triggers (LLM → agent)

* Two-line trigger; optional reply message ID:

  ```markdown
  # «sticker»
  <SET SHORT NAME>
  <STICKER NAME>
  ```

  or

  ```markdown
  # «sticker» <MESSAGE_ID>
  <SET SHORT NAME>
  <STICKER NAME>
  ```
* Legacy one-line body (`<STICKER NAME>`) is tolerated during transition.

---

## Live testing tips

* It’s safe to live-test intermediate changes; worst case the agent pauses responding.
* Prefer testing with a single agent and a small, quiet chat.
* Watch logs for:

  * description outcomes: `ok / not_understood / timeout / error`
  * sticker sending: explicit `(set, name)` vs fallback to plain text
* If the bot seems “stuck”, check for repeated download logs; ensure the per-tick budget isn’t set too high.

---

## Common workflows

### Add a new task type

1. Create `handlers/<type>.py`.
2. Register with `@register_task_handler("<type>")`.
3. Add tests in `tests/test_<type>.py`.
4. **Run:** `PYTHONPATH=. pytest -vv`, then commit.

### Extend persona fields

1. Update `register_agents.py` parse/normalize.
2. Thread the new data through `Agent` (optional), then into prompt building if needed.
3. Add a small parsing test.

### Tweaking Gemini behavior

* `llm.py` sets model default to `gemini-2.5-flash-preview-09-2025`.
* Safety is **BLOCK\_NONE** across categories for both SDK (`query`) and REST (`describe_image`).
* If Gemini returns empty text, inspect `_log_safety_findings` output first.

---

## Conventions

* Keep handlers small; push formatting and utilities into helpers.
* Small PRs; one change at a time.
* Commit discipline:

  1. `PYTHONPATH=. pytest -vv`
  2. Commit with a focused message
* Prefer adding a test alongside any non-trivial logic change.

---

## Roadmap (developer-facing)

* Switch Gemini prompts to **role-structured** contents (`system`, `user`, optional `assistant`) in `query()`.
* Curated image description store layered above disk cache.
* Light metrics around description rates and cache hit ratios.
