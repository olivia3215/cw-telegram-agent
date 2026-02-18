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
| `CINDY_ADMIN_CONSOLE_SSL_CERT` | _(unset)_ | Path to SSL certificate file for HTTPS (optional, requires `SSL_KEY`) |
| `CINDY_ADMIN_CONSOLE_SSL_KEY` | _(unset)_ | Path to SSL private key file for HTTPS (optional, requires `SSL_CERT`) |

Example shell configuration:

```bash
export CINDY_ADMIN_CONSOLE_ENABLED=true
export CINDY_AGENT_LOOP_ENABLED=true       # optional: false to pause the bot
export CINDY_ADMIN_CONSOLE_HOST=127.0.0.1
export CINDY_ADMIN_CONSOLE_PORT=5001
```

Start the agent with your normal workflow (`./run.sh`), then open `http://HOST:PORT/admin`.

**Enabling HTTPS (Optional)**

To secure the admin console with HTTPS:

1. Generate SSL certificates:
   ```bash
   # Self-signed certificate (for development/personal use)
   openssl req -x509 -newkey rsa:4096 -nodes \
     -out certs/cert.pem -keyout certs/key.pem -days 365 \
     -subj "/CN=localhost"
   ```

2. Configure SSL in your shell or `.env`:
   ```bash
   export CINDY_ADMIN_CONSOLE_SSL_CERT="$(pwd)/certs/cert.pem"
   export CINDY_ADMIN_CONSOLE_SSL_KEY="$(pwd)/certs/key.pem"
   ```

3. Restart the server and access via HTTPS:
   ```bash
   ./run.sh restart
   open https://localhost:5001/admin
   ```

**Note:** Self-signed certificates will trigger browser security warnings. For production deployments with public access, consider using a reverse proxy (Nginx) with Let's Encrypt certificates. See [HTTPS.md](HTTPS.md) for quick setup and remote-access options.

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
  - **Profile** — View and edit agent profile information (name, username, bio, birthday, profile photos). Additional profile photos are loaded on demand when paging to avoid timeouts.
  - **Contacts** — Manage agent's Telegram contacts
  - **Parameters** — View and manage agent configuration parameters
  - **Memories** — View and manage global agent memories (visible across all conversations)
  - **Intentions** — View and manage agent intentions
  - **Documents** — Manage agent-specific documentation
  - **Memberships** — View and manage agent's channel/group memberships
  - **Media** — Manage agent's media library (Saved Messages and profile photos)
- **Conversations** — Conversation management with subtabs:
  - **Notes** — View and manage per-user notes (conversation-specific memories) for specific conversation partners
  - **Conversation LLM** — Override LLM model for specific conversations
  - **Plans** — View and manage channel-specific plans
  - **Conversation** — View conversation history, edit summaries, trigger summarization, and optionally show task execution logs interleaved with messages
  - **XSend** — Trigger agent action in a conversation with specific instructions
  - **Work Queue** — View and manage pending tasks in the conversation's task graph

### Conversation Tab - Task Logging

The **Conversation** tab includes an optional **"Show Task Log"** checkbox that interleaves task execution logs with conversation messages, providing visibility into agent actions.

**Features:**
- **Task Execution Visibility**: See all non-visible tasks (think, retrieve, remember, note, plan, intend, summarize, etc.) executed by the agent
- **Chronological Interleaving**: Logs are sorted chronologically with messages for context
- **Visual Distinction**: Task logs appear with a pink background to distinguish them from messages
- **Smart Filtering**: 
  - Only shows successful tasks (failed tasks are not displayed)
  - Excludes visible tasks that are already in the conversation (send, sticker, react, photo)
  - Only shows logs after the last conversation summary (or all logs if no summaries exist)
- **Task Details**: Each log entry shows:
  - Action type (e.g., THINK, RETRIEVE, REMEMBER)
  - Task identifier for debugging
  - Timestamp in agent's timezone
  - Task parameters and details
- **Download Integration**: The "Download Conversation" feature respects the checkbox state and includes interleaved logs in the exported HTML

**Use Cases:**
- Debug agent behavior by seeing exactly what tasks were executed
- Understand the agent's thought process (think tasks)
- See what information was retrieved from URLs (retrieve tasks)
- Track when memories, notes, plans, and intentions were created
- Verify that summaries were generated at expected times

**Technical Details:**
- Task logs are stored in the `task_execution_log` database table
- Logs are retained for 14 days and automatically cleaned up
- All task executions are logged except `wait`/`delay` tasks
- Failed task executions are logged but not displayed in the UI (available via database queries)


### Work Queue Tab

The **Work Queue** tab (under Conversations) provides a read-only view of the task graph for a specific conversation, allowing you to monitor and manage pending agent tasks.

**Features:**
- **Task Graph Visualization**: View all tasks in the conversation's work queue with their current status
- **Status Summary**: Color-coded counts of tasks by status (pending, active, done, failed, cancelled)
- **Task Details**: See task IDs, types, parameters, dependencies, and current status
- **Context Information**: View the graph ID, agent details, channel information, and conversation type
- **Delete Tasks**: Remove all pending tasks from the work queue with a single action

**Task Information Displayed:**
- **ID**: Unique task identifier
- **Type**: Task type (send, sticker, wait, received, etc.)
- **Status**: Current execution status with color-coded indicator
- **Dependencies**: Other tasks that must complete before this task runs
- **Parameters**: Task-specific parameters (e.g., message text, sticker names, delays)

**Deleting the Work Queue:**
The "Delete All Pending Tasks" button clears all tasks from the conversation's work queue. This is useful for:
- Canceling a long queue of pending actions
- Resetting the conversation state when the agent gets stuck
- Clearing outdated tasks after manual intervention

**Note**: Deleting the work queue is a destructive operation and requires confirmation. The agent will create a new task graph when the next message is received.

### Agents Tab - Media Management

The **Media** subtab (under Agents) provides comprehensive management of an agent's media library, including photos, videos, and stickers from Saved Messages and profile photos.

**Features:**
- **Media Library View**: Grid display of all media from the agent's Saved Messages and profile photos
- **Upload Media**: Add new media via file picker or drag-and-drop
- **Profile Picture Management**: 
  - Toggle photos, videos, and stickers as profile pictures with a checkbox
  - When unchecked, media is automatically saved to Saved Messages before removal from profile
  - Multiple profile photos supported (loaded on demand in Profile and partner profile views)
- **Description Editing**: Click any description to edit inline (Ctrl+Enter or blur to save)
- **AI Refresh**: Regenerate descriptions using AI by clearing the cache
- **Delete Media**: Remove media from Saved Messages (requires confirmation)
- **Save from Conversations**: Use the "Save to Agent Media" button in any conversation to capture media to the agent's library

**Media Types Supported:**
- **Photos** ✅ (can be profile picture)
- **Videos** ✅ (can be profile picture)
- **Stickers** ✅ (can be profile picture)
- **Audio** ❌ (cannot be profile picture)
- **Documents** ❌ (cannot be profile picture)

**Save from Conversations Workflow:**
1. Navigate to Conversations → Select conversation → View messages with media
2. Click "Save to Agent Media" button on any media item
3. Media is uploaded to agent's Saved Messages
4. If cached in `state/media/`, it's automatically promoted to `{agent.config_directory}/media/` for permanent storage
5. Media appears in the agent's Media tab

**Technical Details:**
- Media is deduplicated by `unique_id` (if same media exists in both Saved Messages and profile photos, shown once)
- Descriptions are cached in MySQL (`state/media/`) or config directories (`{config_dir}/media/`)
- Profile photo operations use Telegram's `UploadProfilePhotoRequest` and `DeletePhotosRequest`
- The "Save from Conversations" feature promotes media from transient state cache to permanent config storage

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
- `src/admin_console/agents/media.py` — Agent media management routes
- `src/admin_console/agents/conversation_media.py` — Conversation media serving and save endpoint
- `src/db/available_llms.py` — Database operations for LLM management
- `src/media_editor.py` — Blueprint, routes, and AI integrations
- `templates/admin_console.html` — Full admin console interface with tabs (Global, Agents, Conversations)
- `static/js/admin_console_agents.js` — Agent media management UI
- `static/js/admin_console_conversations.js` — Conversation view with media save functionality

Feel free to update this document as new capabilities are added. ***
