# New Deployment Guide

This guide walks through deploying cw-telegram-agent on a fresh system from scratch.

## Prerequisites

Before starting, ensure you have:

- ✅ **Python 3.13** installed
- ✅ **Cairo library** installed (for animated sticker rendering)
  - macOS: `brew install cairo`
  - Ubuntu/Debian: `sudo apt-get install libcairo2-dev pkg-config`
- ✅ **Git** installed (to clone the repository)
- ✅ **MySQL** (optional, for database storage instead of filesystem)

## Step 1: Clone the Repository

```bash
git clone https://github.com/olivia3215/cw-telegram-agent.git
cd cw-telegram-agent
```

## Step 2: Create Virtual Environment

```bash
python3.13 -m venv venv
source venv/bin/activate
```

## Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

## Step 4: Obtain API Keys

You'll need API keys from several services:

### Google Gemini API Key (Required)
1. Visit [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Sign in with your Google account
3. Click "Get API Key" and create a new key
4. Copy the generated API key

### Telegram API Credentials (Required)
1. Visit [Telegram API Development Tools](https://my.telegram.org/apps)
2. Log in with your Telegram account
3. Click "Create New Application"
4. Fill in the required details
5. Copy your `API ID` and `API Hash`

### Grok API Key (Optional)
Only needed if using Grok LLM for agent responses:
1. Visit [console.x.ai](https://console.x.ai)
2. Create an account and generate an API key

### OpenRouter API Key (Optional)
Only needed if using OpenRouter LLM for agent responses:
1. Visit [OpenRouter](https://openrouter.ai)
2. Create an account and generate an API key

## Step 5: Configure Environment

Create or edit your `.env` file in the project root:

```bash
# Basic configuration
export CINDY_AGENT_STATE_DIR="$(pwd)/state"
export CINDY_AGENT_CONFIG_PATH="$(pwd)/samples:$(pwd)/configdir"

# API Keys (replace with your actual keys)
export GOOGLE_GEMINI_API_KEY="your_gemini_api_key_here"
export TELEGRAM_API_ID="your_telegram_api_id_here"
export TELEGRAM_API_HASH="your_telegram_api_hash_here"

# Optional API keys
export GROK_API_KEY="your_grok_api_key_here"  # If using Grok
export OPENROUTER_API_KEY="your_openrouter_api_key_here"  # If using OpenRouter

# Python path (required for running scripts)
export PYTHONPATH="$(pwd)/src"

# Admin Console configuration
export CINDY_PUPPET_MASTER_PHONE="+15551234567"  # Your dedicated puppet master phone
export CINDY_ADMIN_CONSOLE_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CINDY_ADMIN_CONSOLE_ENABLED=true
export CINDY_ADMIN_CONSOLE_HOST=127.0.0.1  # localhost only, or 0.0.0.0 for network access
export CINDY_ADMIN_CONSOLE_PORT=5001
```

**Load the environment:**
```bash
source .env
```

## Step 6: MySQL Database Setup (Optional)

If you want to use MySQL instead of filesystem storage:

### Install MySQL
```bash
# Ubuntu/Debian
sudo apt-get install mysql-server

# macOS
brew install mysql
```

### Create Database
```bash
mysql -u root -p
```

```sql
CREATE DATABASE cindy_telegram_agent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'cindy_agent'@'localhost' IDENTIFIED BY 'your_secure_password';
GRANT ALL PRIVILEGES ON cindy_telegram_agent.* TO 'cindy_agent'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### Configure MySQL in .env
```bash
export CINDY_AGENT_MYSQL_HOST=localhost
export CINDY_AGENT_MYSQL_PORT=3306
export CINDY_AGENT_MYSQL_DATABASE=cindy_telegram_agent
export CINDY_AGENT_MYSQL_USER=cindy_agent
export CINDY_AGENT_MYSQL_PASSWORD='your_secure_password'
export CINDY_AGENT_MYSQL_POOL_SIZE=5
export CINDY_AGENT_MYSQL_POOL_TIMEOUT=30
```

### Create Schema
```bash
source .env
PYTHONPATH=src python scripts/create_mysql_schema.py
```

## Step 7: Configure Agents

Create agent configuration files in `samples/agents/` or your custom config directory.

Example `samples/agents/MyAgent.md`:

```markdown
# Agent Name
MyAgent

# Agent Phone
+15551234567

# Agent Timezone
America/Los_Angeles

# LLM
gemini-2.0-flash

# Role Prompt
Chatbot

# Agent Instructions
You are a helpful and friendly conversational AI assistant. Be concise and engaging.
```

See [samples/README.md](samples/README.md) for detailed configuration options.

## Step 8: Log in Telegram Sessions

**Important:** You must log in to Telegram for each agent phone number before starting the server.

```bash
./telegram_login.sh
```

Follow the prompts:
1. Enter phone number (including country code, e.g., +1234567890)
2. Enter the verification code sent to your Telegram account
3. If 2FA is enabled, enter your 2FA password
4. Repeat for each agent and the puppet master account

## Step 9: Generate SSL Certificates (Optional, for HTTPS)

> **Note:** Certificates are NOT included in the repository. You must generate them if you want HTTPS.

### For Development/Personal Use (Self-Signed)

```bash
# Create certs directory
mkdir -p certs

# Generate self-signed certificate (valid 1 year)
openssl req -x509 -newkey rsa:4096 -nodes \
  -out certs/cert.pem -keyout certs/key.pem -days 365 \
  -subj "/CN=localhost"
```

### Configure HTTPS in .env

```bash
export CINDY_ADMIN_CONSOLE_SSL_CERT="$(pwd)/certs/cert.pem"
export CINDY_ADMIN_CONSOLE_SSL_KEY="$(pwd)/certs/key.pem"
```

### For Production

For production deployments:
- Use Let's Encrypt with Certbot for free, trusted SSL certificates
- Configure a reverse proxy (Nginx/Apache) for SSL termination
- See [HTTPS.md](HTTPS.md) for detailed setup and remote-access options

## Step 10: Start the Server

```bash
./run.sh start
```

## Step 11: Access the Admin Console

### HTTP (Default)
```bash
open http://localhost:5001/admin
```

### HTTPS (If configured)
```bash
open https://localhost:5001/admin
```

**Browser Warning:** Self-signed certificates will show a security warning. Click "Advanced" → "Proceed to localhost (unsafe)" for development use.

### First-Time Authentication

On first visit:
1. Click "Send verification code"
2. Check your puppet master Telegram account for a 6-digit code
3. Enter the code in the admin console
4. You'll stay logged in as long as cookies persist and you keep the same `CINDY_ADMIN_CONSOLE_SECRET_KEY`

## Verification Checklist

After deployment, verify:

- ✅ Server starts without errors (`./run.sh status`)
- ✅ Admin console is accessible at configured port
- ✅ Can log in to admin console with OTP
- ✅ Agents appear in the admin console
- ✅ Can send test messages to agents via Telegram
- ✅ Agents respond appropriately
- ✅ (If using HTTPS) Browser connects via https://

## Common Issues

### "Missing environment variables error"
- Verify all required variables are set in `.env`
- Run `source .env` to load them
- Check for typos in variable names

### "Failed to load SSL certificates"
- Verify both `cert.pem` and `key.pem` exist in `certs/` directory
- Check file permissions (should be readable)
- Regenerate certificates if corrupted

### Telegram login issues
- Ensure phone number includes country code (e.g., +1234567890)
- Check that Telegram account is active
- If 2FA is enabled, have your password ready

### Admin console not accessible
- Check that the server is running: `./run.sh status`
- Verify firewall allows connections to configured port
- Check logs: `./run.sh logs`

### Agents not responding
- Check logs for errors: `./run.sh logs`
- Verify API keys are correct
- Ensure agent configuration files are valid
- Check that Telegram sessions are logged in

## Directory Structure After Setup

```
cw-telegram-agent/
├── venv/                  # Virtual environment
├── state/                 # Runtime state (sessions, work queues)
├── certs/                 # SSL certificates (if using HTTPS)
│   ├── cert.pem          # Generated by you
│   └── key.pem           # Generated by you
├── logs/                  # Application logs
├── samples/              # Sample configurations
├── configdir/            # System prompts
├── .env                  # Your environment configuration
└── ...                   # Source code and documentation
```

## Next Steps

After successful deployment:

1. **Configure agents:** Customize agent personalities and behaviors
2. **Import sticker sets:** Use the Media Editor in the admin console
3. **Set up monitoring:** Check logs regularly for issues
4. **Enable HTTPS:** For production or network access
5. **Set up backups:** Back up `state/` directory and MySQL database
6. **Review security:** See [HTTPS.md](HTTPS.md) for HTTPS and remote-access guidance

## Additional Documentation

- **Quick Start:** [README.md](README.md) - Overview and basic setup
- **Admin Console:** [ADMIN_CONSOLE.md](ADMIN_CONSOLE.md) - Detailed console documentation
- **Architecture:** [DESIGN.md](DESIGN.md) - System architecture and internals
- **Developer Guide:** [DEVELOPER.md](DEVELOPER.md) - Development workflows
- **HTTPS Guide:** [HTTPS.md](HTTPS.md) - Quick start, security notes, and remote-access options

## Support

For issues, questions, or feature requests:
- Check the [Troubleshooting](#common-issues) section above
- Review existing GitHub issues
- Create a new issue with detailed information about your problem

## Security Notes

⚠️ **Important Security Considerations:**

- Never commit `.env` file to version control (it's in `.gitignore`)
- Never commit SSL private keys (`certs/key.pem` is in `.gitignore`)
- Keep your API keys secure and rotate them periodically
- For production deployments, use proper SSL certificates (not self-signed)
- Restrict admin console access to trusted networks
- Regularly update dependencies for security patches

---

**Congratulations!** Your cw-telegram-agent deployment should now be running. 🎉
