# Admin Console

The Admin Console is a web application that runs alongside `cw-telegram-agent`. It exposes tooling for administrators, starting with the Media Editor, and lays the groundwork for future management pages (`xsend`, memories, plans, intents, and conversations).

> **TL;DR:** enable it with environment variables, start your agent as usual, then browse to the configured host/port.

---

## Overview

- Tab-based interface rendered at `/admin`
- Media Editor tab is the existing sticker/media curator
- Additional tabs are placeholders for upcoming tooling
- Reuses the same media pipeline and caches as the running agent
- Designed to be embedded in the main process, so edits reflect immediately

---

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `CINDY_ADMIN_CONSOLE_ENABLED` | `true` | Toggle the admin console server |
| `CINDY_AGENT_LOOP_ENABLED` | `true` | Toggle the agent loop; set `false` to run console-only |
| `CINDY_ADMIN_CONSOLE_HOST` | `0.0.0.0` | Host interface to bind (use `127.0.0.1` for local-only) |
| `CINDY_ADMIN_CONSOLE_PORT` | `5001` | Listening port |

Example shell configuration:

```bash
export CINDY_ADMIN_CONSOLE_ENABLED=true
export CINDY_AGENT_LOOP_ENABLED=true       # optional: false to pause the bot
export CINDY_ADMIN_CONSOLE_HOST=127.0.0.1
export CINDY_ADMIN_CONSOLE_PORT=5001
```

Start the agent with your normal workflow (`./run.sh`), then open `http://HOST:PORT/admin`.

---

## Tabs

The admin console provides three main tabs:

- **Global** — Global system management with subtabs:
  - **Media Editor** — The full sticker/media management experience (details below)
  - **Documents** — Document management
  - **Role Prompts** — Role prompt editing
  - **Parameters** — Global system parameters (DEFAULT_AGENT_LLM, MEDIA_MODEL, TRANSLATION_MODEL, etc.)
  - **LLMs** — Manage available LLM models in the database (add, edit, delete, reorder)
- **Agents** — Agent management with subtabs:
  - **Parameters** — View and manage agent configuration parameters
  - **Memories** — View and manage global agent memories (visible across all conversations)
  - **Intentions** — View and manage agent intentions
- **Conversations** — Conversation management with subtabs:
  - **Notes** — View and manage per-user notes (conversation-specific memories) for specific conversation partners
  - **Conversation LLM** — Override LLM model for specific conversations
  - **Plans** — View and manage channel-specific plans
  - **Conversation** — View conversation history, edit summaries, trigger summarization, and delete telepathic messages (messages starting with `⟦think⟧`, `⟦remember⟧`, `⟦intend⟧`, `⟦plan⟧`, `⟦retrieve⟧`, `⟦summarize⟧`, `⟦xsend⟧`, `⟦note⟧`)
  - **XSend** — Trigger agent action in a conversation with specific instructions

---

## Media Editor Tab

The Media Editor provides visual browsing, editing, and management of media descriptions across all agents and directories. It includes:

- Visual browsing of curated and cached media
- Real-time editing with autosave
- AI-powered description generation
- Sticker set import from Telegram
- Media organization (move/delete)

**See [MEDIA_EDITOR.md](MEDIA_EDITOR.md) for comprehensive Media Editor documentation.**

### LLMs Tab

The **LLMs** tab (under Global) provides a centralized interface for managing all available LLM models stored in the MySQL database.

**Features:**
- **View all LLMs**: See all available models with their canonical names, descriptions, and pricing (per 1M tokens)
- **Add LLMs**: Add new models from OpenRouter's popular roleplay models or create custom models
- **Edit LLMs**: Inline editing of model ID, name, description, and pricing
- **Reorder LLMs**: Drag and drop to reorder the list (affects display order in comboboxes)
- **Delete LLMs**: Remove models from the database

**Adding Models:**
- Select from the "Add LLM..." pulldown to choose from OpenRouter's popular roleplay models
- Models already in the database are automatically filtered out
- Select "Custom..." to add a model manually with custom fields
- OpenRouter models automatically populate with pricing and descriptions from the API

**LLM List Usage:**
- The LLM list in the database is used by all comboboxes throughout the admin console:
  - Agent LLM selection
  - Conversation LLM override
  - Global parameters (DEFAULT_AGENT_LLM, MEDIA_MODEL, TRANSLATION_MODEL)
- Models are filtered based on API key availability (e.g., OpenRouter models only shown if OPENROUTER_API_KEY is set)

**Storage:**
- LLMs are stored in the MySQL `available_llms` table
- Migration automatically populates the database from existing hardcoded lists, state files, and agent configurations
- The `state/openrouter_roleplay_models.json` cache file is no longer used for the main LLM list

---

## Troubleshooting

- **Console unreachable** — verify `CINDY_ADMIN_CONSOLE_ENABLED=true` and check logs for binding errors.
- **Agent paused unexpectedly** — ensure `CINDY_AGENT_LOOP_ENABLED` hasn’t been set to `false`.
- **Edits not reflected in conversations** — confirm you’re editing the correct directory (`config` vs `state` media). Shared caches should make changes immediate; look for permission or disk errors in logs.
- **Sticker import failing** — make sure the agent is authenticated to Telegram and the sticker set is public.

---

## Next steps

- Add authentication/authorization controls once more tabs contain sensitive data.
- Flesh out the placeholder tabs with operational tooling.
- Extend documentation here as each feature lands.

For developers, the implementation lives under:

- `src/admin_console/app.py` — Flask factory + background server helper
- `src/admin_console/llms.py` — LLM management routes and API endpoints
- `src/db/available_llms.py` — Database operations for LLM management
- `src/media_editor.py` — Blueprint, routes, and AI integrations
- `templates/admin_console.html` — Full admin console interface with tabs (Global, Agents, Conversations)

Feel free to update this document as new capabilities are added. ***

