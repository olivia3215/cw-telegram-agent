#!/bin/bash

# Telegram Login Wrapper Script
# Launches the telegram_login.py program from src/ directory

# Set up environment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_PATH="$PROJECT_ROOT/venv"

# Check if virtual environment exists
if [ ! -d "$VENV_PATH" ]; then
    echo "Error: Virtual environment not found at $VENV_PATH"
    echo "Please create it with: python3.13 -m venv venv"
    exit 1
fi

# Activate virtual environment and run the script
cd "$PROJECT_ROOT"
source "$VENV_PATH/bin/activate"
if [ -n "$PYTHONPATH" ]; then
    export PYTHONPATH="$PROJECT_ROOT/src:$PYTHONPATH"
else
    export PYTHONPATH="$PROJECT_ROOT/src"
fi

# Run telegram_login.py with all arguments
python "$PROJECT_ROOT/src/telegram_login.py" "$@"
