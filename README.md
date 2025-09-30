# cw-telegram-agent

Conversational Telegram agents powered by an LLM. This README covers how to **set up**, **configure**, and **run** the server. For architecture, internals, and developer workflows, see `DESIGN.md` and `DEVELOPER.md`.

---

## Requirements

* **Python 3.13**
* A Telegram account (for each agent persona you run)
* A Google Gemini API key (for image/sticker descriptions)

---

## Quick start

### 1) Create and activate a virtual environment

```bash
# from the repo root
python3.13 -m venv venv
source venv/bin/activate
# on Windows PowerShell:
# .\venv\Scripts\Activate.ps1
```

### 2) Install dependencies

```bash
pip install -r requirements.txt
```

### 3) Configure environment

Set these environment variables (example uses a local `./state` dir):

```bash
export CINDY_AGENT_STATE_DIR="$(pwd)/state"
export CONFIG_DIRS="$(pwd)/samples"
export GOOGLE_GEMINI_API_KEY="your_api_key_here"
```

For multiple configuration directories, separate them with colons:
```bash
export CONFIG_DIRS="$(pwd)/samples:$(pwd)/custom-configs"
```

Optional tuning:

```bash
# Number of new AI description attempts per received task (cache hits are free)
export MEDIA_DESC_BUDGET_PER_TICK=8

# Enable comprehensive LLM prompt/response logging for debugging
export GEMINI_DEBUG_LOGGING=true
```

### 4) Log in Telegram sessions

Run the helper once per persona to establish Telegram sessions:

```bash
python telegram_login.py
```

### 5) Start the agent loop

```bash
python run.py
```

The loop connects, processes unread messages, plans with the LLM, and executes **one task per tick**.

---

## Personas (Configuration Directories)

Create one markdown file per agent in the `agents` subdirectory of each config directory, e.g. `samples/agents/Wendy.md`:

```markdown
# Agent Name
Wendy

# Agent Phone
+15551234567

# Role Prompt
WendyDancer

# Agent Instructions
Write how you want the agent to behave and respond.

# Agent Sticker Sets
WendyDancer   # optional; list of sticker sets (one per line)
CindyPainter

# Agent Stickers
WendyDancer :: 😉   # optional; explicit curated stickers (one per line)
CindyPainter :: 😀
```

Notes:

* **Required fields:** `Agent Name`, `Agent Phone`, `Role Prompt`, `Agent Instructions`.
* **Optional fields:** `Agent Sticker Sets`, `Agent Stickers`.
  You may omit these entirely to disable sticker support.
* **Reserved names:** Agent names cannot be `media` (reserved for system directories).

> Internals about sticker trigger syntax and LLM task formats are documented in `DESIGN.md` (not needed for basic use).

---

## Configuration Directory Structure

Each configuration directory (specified in `CONFIG_DIRS`) should contain:

```
config-dir/
├── agents/          # Agent definitions (.md files)
└── prompts/         # System prompts (.md files)
```

**Multiple directories:** You can specify multiple config directories separated by colons in `CONFIG_DIRS`. The system will search for agents and prompts in all directories, with earlier directories taking precedence for duplicate names.

**Default location:** If `CONFIG_DIRS` is not set, the system defaults to the `samples` directory.

---

## Media descriptions (high level)

The agent enriches its prompt by describing recent **photos and stickers**. Descriptions are cached in memory and on disk to avoid repeated work. A **per-tick budget** limits how many **new** descriptions are attempted each turn; cache hits do not consume budget.

You generally don’t need to configure anything for this beyond `GOOGLE_GEMINI_API_KEY`.

---

## Troubleshooting

* **Agent seems slow or idle**

  * Check logs for repeated download lines. Consider lowering `MEDIA_DESC_BUDGET_PER_TICK`.
  * Ensure persona files in your configuration directories include all **required** fields.

* **Sticker appeared as plain text**

  * The requested sticker may not be resolvable at send time. The agent falls back to sending the sticker **name** as text. This isn’t harmful; it just indicates the sticker doc wasn’t found.

* **No LLM output / empty responses**

  * Verify `GOOGLE_GEMINI_API_KEY` is valid.
  * See `DEVELOPER.md` for logging tips and model settings.
  * Enable `GEMINI_DEBUG_LOGGING=true` to see complete prompts and responses for debugging.

* **Debugging LLM behavior**

  * Set `GEMINI_DEBUG_LOGGING=true` to log complete prompts sent to Gemini and full responses received.
  * This will show system instructions, conversation history, and detailed response metadata.

---

## More docs

* **Architecture & design:** `DESIGN.md`
* **Developer guide:** `DEVELOPER.md`
