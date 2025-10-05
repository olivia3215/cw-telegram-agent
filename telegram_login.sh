#!/bin/bash

# Telegram Login Wrapper Script
# Launches the telegram_login.py program from src/ directory

# Set up environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_PATH="$PROJECT_ROOT/venv"
ENV_FILE="$PROJECT_ROOT/.env"

# Source the shared library
source "$SCRIPT_DIR/scripts/lib.sh"

# Check if virtual environment exists
check_venv

# Set up environment using shared function
setup_environment

# Run telegram_login.py with all arguments
python "$PROJECT_ROOT/src/telegram_login.py" "$@"
