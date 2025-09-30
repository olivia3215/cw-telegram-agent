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
export TELEGRAM_API_ID="your_api_id_here"
export TELEGRAM_API_HASH="your_api_hash_here"
```

For multiple configuration directories, separate them with colons:
```bash
export CONFIG_DIRS="$(pwd)/samples:$(pwd)/custom-configs"
```

#### Obtaining API Keys

**Google Gemini API Key (`GOOGLE_GEMINI_API_KEY`)**

Required for image and sticker descriptions and for composing the LLM responses for the agents. To obtain:

1. Visit [Google AI Studio](https://aistudio.google.com/app/apikey) and sign in with your Google account
2. Click "Get API Key" and create a new key
3. Copy the generated API key
4. Set it as an environment variable:
   ```bash
   export GOOGLE_GEMINI_API_KEY="your_actual_api_key_here"
   ```

**Telegram API Credentials (`TELEGRAM_API_ID` and `TELEGRAM_API_HASH`)**

Required for Telegram authentication. To obtain:

1. Visit [Telegram API Development Tools](https://my.telegram.org/apps) and log in with your Telegram account
2. Click "Create New Application"
3. Fill in the required details:
   - **App title**: Your application name (e.g., "My CW Telegram Agent")
   - **Short name**: A short identifier (e.g., "my-cw-telegram-agent")
   - **Platform**: Choose "Desktop" or appropriate platform
   - **Description**: Brief description of your application
4. After submission, you'll receive:
   - **App ID**: This is your `TELEGRAM_API_ID`
   - **App Hash**: This is your `TELEGRAM_API_HASH`
5. Set them as environment variables:
   ```bash
   export TELEGRAM_API_ID="your_actual_api_id_here"
   export TELEGRAM_API_HASH="your_actual_api_hash_here"
   ```

**Security Note**: Never commit these API keys to version control. Consider using a `.env` file or your shell's environment configuration (e.g., `~/.bashrc`, `~/.zshrc`) for persistent storage.

### Optional tuning

```bash
# Number of new AI description attempts per tick (cache hits are free)
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
  You may omit these entirely.
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

* **Missing environment variables error**

  * Ensure all required environment variables are set: `GOOGLE_GEMINI_API_KEY`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and `CINDY_AGENT_STATE_DIR`.
  * See the "Obtaining API Keys" section above for detailed instructions on getting these credentials.

* **Agent seems slow or idle**

  * Check logs for repeated download lines. Consider lowering `MEDIA_DESC_BUDGET_PER_TICK`.
  * Ensure persona files in your configuration directories include all **required** fields.

* **Sticker appeared as plain text**

  * The requested sticker may not be resolvable at send time. The agent falls back to sending the sticker **name** as text. This isn't harmful; it just indicates the sticker doc wasn't found.

* **No LLM output / empty responses**

  * Verify `GOOGLE_GEMINI_API_KEY` is valid.
  * See `DEVELOPER.md` for logging tips and model settings.
  * Enable `GEMINI_DEBUG_LOGGING=true` to see complete prompts and responses for debugging.

* **Telegram login issues**

  * Verify your `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` are correct.
  * Ensure you're using the phone number associated with your Telegram account.
  * Check that your Telegram account has 2FA disabled or be prepared to enter your 2FA password during login.

* **Debugging LLM behavior**

  * Set `GEMINI_DEBUG_LOGGING=true` to log complete prompts sent to Gemini and full responses received.
  * This will show system instructions, conversation history, and detailed response metadata.

---

## More docs

* **Architecture & design:** `DESIGN.md`
* **Developer guide:** `DEVELOPER.md`
