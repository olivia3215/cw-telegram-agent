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

- **Media Editor** — The full sticker/media management experience (details below).
- **xsend** *(coming soon)* — Placeholder for future xsend tooling.
- **Global Memories**, **Local Memories** *(coming soon)* — Planned memory management views.
- **Plans**, **Intents**, **Conversations** *(coming soon)* — Planned operational dashboards.

Only the Media Editor tab is implemented today; the placeholders ensure navigation is in place for future work.

---

## Media Editor Tab

The Media Editor provides:

- Visual browsing of curated and cached media across directories
- Real-time editing of descriptions with autosave and status updates
- AI regeneration (via the same Gemini-powered pipeline as the agent)
- Importing entire sticker sets from Telegram
- Moving or deleting media across directories
- TGS previews, GIF/video playback, and audio players

### Directory Types

The dropdown enumerates locations yielded by `CINDY_AGENT_CONFIG_PATH` (`{config_dir}/media/`) and the AI cache (`CINDY_AGENT_STATE_DIR/media`). Because the admin console shares the same in-memory `DirectoryMediaSource` objects as the agent, updates via the UI are immediately visible to running workers.

### Editing Workflow

1. Select a directory.
2. Pick an item and edit the description textarea.
3. Autosave triggers after 1s of inactivity; status transitions `Saved → Saving… → Saved` (or error).
4. Edits mark the item as `curated` and clear previous failure reasons.
5. Use **Refresh from AI** to trigger on-demand regeneration (consumes description budget).

### Moving / Deleting

- **Move to…** uses shared media sources to relocate JSON + media to the chosen directory.
- **Delete** removes both JSON and associated media assets, flushing caches across the app.

### Sticker Set Import

1. Choose a destination directory.
2. Enter a sticker-set short name (e.g. `WendyDancer`).
3. Click **Import Set** — the console downloads each sticker, persists files, and caches metadata.
4. Errors or skipped entries are reported inline; imported items appear in the grid once finished.

> The import flow leverages Telegram sessions configured for your agent; run `./telegram_login.sh` ahead of time for any new accounts.

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
- `templates/admin_console.html` — Tab shell + media editor partial

Feel free to update this document as new capabilities are added. ***

