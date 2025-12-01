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

- **Media Editor** — The full sticker/media management experience (details below).
- **Agents** — Agent management with subtabs:
  - **Parameters** — View and manage agent configuration parameters
  - **Memories** — View and manage global agent memories (visible across all conversations)
  - **Intentions** — View and manage agent intentions
- **Conversations** — Conversation management with subtabs:
  - **Curated Memories** — View and manage per-user curated memories for specific conversation partners
  - **Conversation LLM** — Override LLM model for specific conversations
  - **Plans** — View and manage channel-specific plans
  - **Conversation** — View conversation history, edit summaries, trigger summarization, and delete telepathic messages (messages starting with `⟦think⟧`, `⟦remember⟧`, `⟦intend⟧`, `⟦plan⟧`, `⟦retrieve⟧`, `⟦summarize⟧`)
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
- `src/media_editor.py` — Blueprint, routes, and AI integrations
- `templates/admin_console.html` — Full admin console interface with tabs (Media Editor, Agents, Conversations)

Feel free to update this document as new capabilities are added. ***

