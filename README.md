# cw-telegram-agent

Conversational Telegram agents powered by an LLM. This README covers how to **set up**, **configure**, and **run** the server. For architecture, internals, and developer workflows, see [DESIGN.md](DESIGN.md) and [DEVELOPER.md](DEVELOPER.md).

---

## Requirements

* **Python 3.13**
* **Cairo library** (for animated sticker rendering)
  - macOS: `brew install cairo`
  - Ubuntu/Debian: `sudo apt-get install libcairo2-dev pkg-config`
  - Other Linux: Install cairo development packages for your distribution
* A Telegram account (for each agent persona you run)
* A Google Gemini API key (for image/sticker descriptions and video analysis, and for composing LLM responses if using Gemini)
* A Grok API key (optional, only required if using Grok LLM for agent responses)

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
export CINDY_AGENT_CONFIG_PATH="$(pwd)/samples:$(pwd)/configdir"
export GOOGLE_GEMINI_API_KEY="your_api_key_here"
export GROK_API_KEY="your_api_key_here"  # Optional, only needed if using Grok LLM
export TELEGRAM_API_ID="your_api_id_here"
export TELEGRAM_API_HASH="your_api_hash_here"
```

For multiple configuration directories, separate them with colons:
```bash
export CINDY_AGENT_CONFIG_PATH="$(pwd)/samples:$(pwd)/configdir:$(pwd)/custom-configs"
```

**Note:** All Python commands in this guide require `PYTHONPATH=src` to be set, as the source code is organized in a `src/` directory. You can either set this for each command or add it to your shell environment.

#### Obtaining API Keys

**Google Gemini API Key (`GOOGLE_GEMINI_API_KEY`)**

Required for image and sticker descriptions and video analysis. Also required if using Gemini LLM for agent responses (default). To obtain:

1. Visit [Google AI Studio](https://aistudio.google.com/app/apikey) and sign in with your Google account
2. Click "Get API Key" and create a new key
3. Copy the generated API key
4. Set it as an environment variable:
   ```bash
   export GOOGLE_GEMINI_API_KEY="your_actual_api_key_here"
   ```

**Grok API Key (`GROK_API_KEY`)**

Required only if using Grok LLM for agent responses. To obtain:

1. Visit [console.x.ai](https://console.x.ai) and create an account
2. Generate an API key from your account settings
3. Copy the generated API key
4. Set it as an environment variable:
   ```bash
   export GROK_API_KEY="your_actual_api_key_here"
   ```

See the [samples/README.md](samples/README.md) documentation for how to specify which LLM to use in agent configurations.

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

### 4) Set up MySQL database (optional, for MySQL storage backend)

If you want to use MySQL for data storage instead of the filesystem, you'll need to set up a MySQL database:

**Installing MySQL:**

- **Ubuntu/Debian**: `sudo apt-get install mysql-server`
- **macOS**: `brew install mysql` or download from [mysql.com](https://dev.mysql.com/downloads/mysql/)
- **Windows**: Download installer from [mysql.com](https://dev.mysql.com/downloads/mysql/)

**Creating the database and user:**

First, decide on your database name, username, and password. Then:

```bash
mysql -u root -p
```

Then run these SQL commands (replace `your_database_name`, `your_username`, and `your_password_here` with your chosen values):

```sql
CREATE DATABASE your_database_name CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'your_username'@'localhost' IDENTIFIED BY 'your_password_here';
GRANT ALL PRIVILEGES ON your_database_name.* TO 'your_username'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

**Note:** The database name, username, and password you choose here should match the values you set in the environment variables below.

**Creating the schema:**

After the MySQL migration is implemented, you'll be able to create the schema using:

```bash
# Option 1: Using the Python script (when available)
# This will use the database connection settings from your environment variables
PYTHONPATH=src python scripts/create_mysql_schema.py

# Option 2: Using SQL file directly (replace with your database name and username)
mysql -u your_username -p your_database_name < scripts/mysql_schema.sql
```

**Configuring MySQL connection:**

Add these environment variables (or add them to your `.env` file):

```bash
export CINDY_AGENT_MYSQL_HOST=localhost
export CINDY_AGENT_MYSQL_PORT=3306
export CINDY_AGENT_MYSQL_DATABASE=your_database_name
export CINDY_AGENT_MYSQL_USER=your_username
export CINDY_AGENT_MYSQL_PASSWORD=your_password_here
export CINDY_AGENT_MYSQL_POOL_SIZE=5
export CINDY_AGENT_MYSQL_POOL_TIMEOUT=30

```

Replace `your_database_name`, `your_username`, and `your_password_here` with the values you used when creating the database.

**Note:** The system uses MySQL for storing agent data (memories, intentions, plans, summaries, schedules, translations, and media metadata). Media files, Telegram sessions, and work queue state always remain in the filesystem.

### 5) Set up test database (for running tests)

To run tests, you'll need a separate test database. Tests automatically use `CINDY_AGENT_MYSQL_TEST_*` environment variables to prevent accidental use of the production database.

**Creating the test database:**

```bash
mysql -u root -p
```

Then run these SQL commands (replace `test_cindy_agent`, `test_user`, and `test_password` with your chosen values):

```sql
CREATE DATABASE test_cindy_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'test_user'@'localhost' IDENTIFIED BY 'test_password';
GRANT ALL PRIVILEGES ON test_cindy_agent.* TO 'test_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

**Creating the test database schema:**

The schema creation script uses production database variables (`CINDY_AGENT_MYSQL_*`). To create the test database schema, temporarily set the test variables as production variables:

```bash
# Set test database environment variables first (as shown above)
export CINDY_AGENT_MYSQL_TEST_DATABASE=test_cindy_agent
export CINDY_AGENT_MYSQL_TEST_USER=test_user
export CINDY_AGENT_MYSQL_TEST_PASSWORD=test_password
export CINDY_AGENT_MYSQL_TEST_HOST=localhost
export CINDY_AGENT_MYSQL_TEST_PORT=3306

# Temporarily use test variables as production variables for schema creation
export CINDY_AGENT_MYSQL_DATABASE=$CINDY_AGENT_MYSQL_TEST_DATABASE
export CINDY_AGENT_MYSQL_USER=$CINDY_AGENT_MYSQL_TEST_USER
export CINDY_AGENT_MYSQL_PASSWORD=$CINDY_AGENT_MYSQL_TEST_PASSWORD
export CINDY_AGENT_MYSQL_HOST=$CINDY_AGENT_MYSQL_TEST_HOST
export CINDY_AGENT_MYSQL_PORT=$CINDY_AGENT_MYSQL_TEST_PORT

# Create the schema
PYTHONPATH=src python scripts/create_mysql_schema.py

# Optionally unset the temporary production variables after (or leave them if you want to use test DB for development)
```

**Configuring test database connection:**

Add these environment variables for tests (or add them to your `.env` file):

```bash
export CINDY_AGENT_MYSQL_TEST_DATABASE=test_cindy_agent
export CINDY_AGENT_MYSQL_TEST_USER=test_user
export CINDY_AGENT_MYSQL_TEST_PASSWORD=test_password
export CINDY_AGENT_MYSQL_TEST_HOST=localhost
export CINDY_AGENT_MYSQL_TEST_PORT=3306
```

**Important safety features:**

- Tests automatically use `CINDY_AGENT_MYSQL_TEST_*` variables instead of production `CINDY_AGENT_MYSQL_*` variables
- The test database name **must contain 'test'** (case-insensitive) or tests will fail
- Production database variables are ignored when running tests
- Tests will fail immediately if attempting to use a database name without 'test'

**Running tests:**

```bash
# Set PYTHONPATH and run tests
export PYTHONPATH=src
pytest
```

### 6) Log in Telegram sessions

Run the helper to establish Telegram sessions:

```bash
./telegram_login.sh
```

### 7) Start the agent loop

```bash
./run.sh start
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

# Agent Timezone
America/Los_Angeles   # optional; IANA timezone (e.g., America/New_York, Pacific/Honolulu)

# LLM
gemini-2.0-flash   # optional; LLM to use (gemini, grok, or specific model name)

# Role Prompt
Chatbot

# Agent Instructions
Write how you want the agent to behave and respond.

# Agent Sticker Sets
WendyDancer   # optional; list of sticker sets (one per line)
CindyPainter

# Agent Stickers
WendyDancer :: 😉   # optional; explicit curated stickers (one per line)
CindyPainter :: 😀
```

### Role Prompts

Role prompts define the core personality and behavior patterns for your agent. You can use single or multiple role prompts to create complex personalities.

**Basic usage:**
```markdown
# Role Prompt
Chatbot
```

**Multiple role prompts:**
```markdown
# Role Prompt
Chatbot
Student
```

**Available role prompts include:**

The basic role prompts are as follows. You should include one of them, or write your own equivalent
- `Chatbot` – Build a concise, personality-driven conversational style
- `Roleplay` – Cooperative storytelling with immersive narration rules
- `Adventure` – Dungeon Master-style narrative adventures with choice-driven turns

In addition the following prompts give agents additional capabilities
- `Person` – Encourage everyday, human-like small talk and life details
- `Memory` – Teach the agent how and when to record long-term memories
- `Retrieve` – Enable web retrieval tasks for fresh information
- `XSend` – Allow cross-channel intents to the agent’s future self

> **Detailed documentation:** See [samples/README.md](samples/README.md) for comprehensive information about role prompts, including agent-specific prompts, loading priority, and examples.

Notes:

* **Required fields:** `Agent Name`, `Agent Phone`, `Agent Instructions`.
* **Optional fields:** `Agent Timezone`, `LLM`, `Agent Sticker Sets`, `Agent Stickers`.
  You may omit these entirely.
  
  The `LLM` field specifies which LLM to use:
  - `gemini` or names starting with `gemini` (e.g., `gemini-2.0-flash`) - uses Gemini LLM
  - `grok` or names starting with `grok` (e.g., `grok-4-fast-non-reasoning`) - uses Grok LLM
  - If omitted, defaults to Gemini with `gemini-3-flash-preview`
* **Reserved names:** Agent display names and config file names (the filename without `.md`) cannot be `media` (reserved for system directories).
* **Uniqueness:** Both agent display names (from the `Agent Name` field) and config file names (the filename without `.md`) must be unique across all config directories. If two config files have the same filename but different display names, only the first one will be registered to prevent state directory conflicts.

> Internals about sticker trigger syntax and LLM task formats are documented in [DESIGN.md](DESIGN.md) (not needed for basic use).

---

## Configuration Directory Structure

Each configuration directory (specified in `CINDY_AGENT_CONFIG_PATH`) should contain:

```
config-dir/
├── agents/          # Agent definitions (.md files)
└── prompts/         # System prompts (.md files)
```

**Multiple directories:** You can specify multiple config directories separated by colons in `CINDY_AGENT_CONFIG_PATH`. The system will search for agents and prompts in all directories, with earlier directories taking precedence for duplicate names.

**Important:** Both agent display names (from the `Agent Name` field) and config file names (the filename without `.md`) must be unique across all config directories. If duplicate display names or config file names are detected, only the first occurrence will be registered, and subsequent duplicates will be skipped with a warning or error logged.

**Prompt separation:** Shared system prompts (e.g., `Instructions.md`, `Task-*.md`) are located in `configdir/prompts`. These prompts are essential for the agent's operation and must be included on your configuration path. Sample agent-specific prompts (e.g., `Chatbot.md`, `Roleplay.md`) are located in `samples/prompts`.

**Default location:** If `CINDY_AGENT_CONFIG_PATH` is not set, the system defaults to `samples:configdir`. This ensures that both sample agents and the required shared prompts are available by default.

---

## Media descriptions (high level)

The agent enriches its prompt by describing recent **photos and stickers**. Descriptions are cached in memory and on disk to avoid repeated work. A **per-tick budget** limits how many **new** descriptions are attempted each turn; cache hits do not consume budget.

You generally don't need to configure anything for this beyond `GOOGLE_GEMINI_API_KEY`.

### Curated media descriptions (optional)

You can provide **curated descriptions** for specific media items that override AI-generated descriptions. This is useful for:
- Providing more accurate or context-specific descriptions
- Describing media in a way that aligns with your agent's personality
- Overriding descriptions for frequently-used stickers

Curated descriptions are provided at the global level:
- **Global**: Shared by all agents (`{config_dir}/media/`)

**See [samples/media/README.md](samples/media/README.md) for complete details on curated media descriptions, including directory structure, file format, and examples.**

### Admin Console

The Admin Console serves administrative tooling with multiple tabs for managing agents, conversations, and media. It runs alongside the main agent so edits appear immediately in live conversations.

**Key environment variables**

| Variable | Default | Purpose |
| --- | --- | --- |
| `CINDY_ADMIN_CONSOLE_ENABLED` | `true` | Enable/disable the console server |
| `CINDY_AGENT_LOOP_ENABLED` | `true` | Enable/disable the agent loop (set `false` to run console-only) |
| `CINDY_PUPPET_MASTER_PHONE` | _(unset)_ | Phone number for the dedicated puppet master account. Required to enable the console. |
| `CINDY_ADMIN_CONSOLE_SECRET_KEY` | _(random each run)_ | Flask session secret; set to a fixed value to keep console logins after restarts. |
| `CINDY_ADMIN_CONSOLE_HOST` | `0.0.0.0` | Host interface for the console |
| `CINDY_ADMIN_CONSOLE_PORT` | `5001` | Port for the console |

**Quick start**
1. Configure the puppet master account and session secret (generate the secret once and reuse it in your environment or `.env` file):
   ```bash
   export CINDY_PUPPET_MASTER_PHONE="+15551234567"        # dedicated Telegram account
   # Generate once: python -c 'import secrets; print(secrets.token_urlsafe(32))'
   export CINDY_ADMIN_CONSOLE_SECRET_KEY="copy-the-generated-value-here"
   export CINDY_ADMIN_CONSOLE_ENABLED=true
   export CINDY_AGENT_LOOP_ENABLED=true
   ```
2. Log in to Telegram for the puppet master and each agent:
   ```bash
   ./telegram_login.sh          # logs the puppet master first (if phone set), then all agents
   ```
3. Start the service and open the console:
   ```bash
   ./run.sh
   open http://localhost:5001/admin
   ```

On first visit to the console you'll be prompted to send a six-digit verification code. Click "Send verification code" to have the puppet master Telegram account message itself; enter that code to unlock the UI. Sessions are remembered until you clear cookies or restart without the same `CINDY_ADMIN_CONSOLE_SECRET_KEY`.

The Admin Console provides three main tabs:

- **Media Editor** — Browse, edit, import, and manage media descriptions:
  - Browse and edit media descriptions across all agents and directories
  - Import sticker sets from Telegram with automatic AI-generated descriptions
  - Curate descriptions by manually editing AI-generated content
  - Move items between directories or delete unwanted content
  - Refresh descriptions using the AI pipeline to generate new versions

- **Agents** — Manage agent configuration and data:
  - View and manage agent parameters
  - View and manage global agent memories (visible across all conversations)
  - View and manage agent intentions

- **Conversations** — Manage conversation-specific data:
  - View and manage per-user curated memories for specific conversation partners
  - Override LLM model for specific conversations
  - View and manage channel-specific plans
  - View conversation history, edit summaries, trigger summarization, and delete telepathic messages (internal agent messages visible in telepathic channels)
  - Trigger agent action in a conversation with specific instructions

**See [ADMIN_CONSOLE.md](ADMIN_CONSOLE.md) for comprehensive documentation.**

For detailed information about the script management system and project architecture, see [DESIGN.md](DESIGN.md).

---

## Troubleshooting

* **Missing environment variables error**

  * Ensure all required environment variables are set: `GOOGLE_GEMINI_API_KEY`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and `CINDY_AGENT_STATE_DIR`.
  * If using Grok LLM, also set `GROK_API_KEY`.
  * See the "Obtaining API Keys" section above for detailed instructions on getting these credentials.

* **Agent seems slow or idle**

  * Check logs for repeated download lines. Consider lowering `MEDIA_DESC_BUDGET_PER_TICK`.
  * Ensure persona files in your configuration directories include all **required** fields.

* **No LLM output / empty responses**

  * Verify `GOOGLE_GEMINI_API_KEY` is valid.
  * See [DEVELOPER.md](DEVELOPER.md) for logging tips and model settings.
  * Enable `GEMINI_DEBUG_LOGGING=true` to see complete prompts and responses for debugging.

* **Telegram login issues**

  * Verify your `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` are correct.
  * Ensure you're using the phone number associated with your Telegram account.
  * Check that your Telegram account has 2FA disabled or be prepared to enter your 2FA password during login.

* **Debugging LLM behavior**

  * Set `GEMINI_DEBUG_LOGGING=true` to log complete prompts sent to Gemini and full responses received.
  * Set `GROK_DEBUG_LOGGING=true` to log complete prompts sent to Grok and full responses received.
  * This will show system instructions, conversation history, and detailed response metadata.

---

## More docs

* **Architecture & design:** [DESIGN.md](DESIGN.md)
* **Developer guide:** [DEVELOPER.md](DEVELOPER.md)
* **Curated media descriptions:** [samples/media/README.md](samples/media/README.md)
