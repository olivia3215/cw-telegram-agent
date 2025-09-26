# DESIGN

This document describes the high-level architecture of the Telegram agent, with specific attention to how we build prompts for Gemini and how message/Media context flows through the system.

## High-level data flow

1. **Inbound message (Telegram)** → `handlers/received.py`
2. **Media description injection** (stickers/photos/etc.) → `media_injector.py`
3. **Conversation assembly** → normalized `ChatMsg` records (one per original message), each with ordered `parts`
4. **Prompt build** → `build_gemini_contents(...)` (in `llm.py`)
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
- The **target message** (the one we want a response to) is appended last as a `user` turn.
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
