#!/bin/bash

# Main Agent Server Startup Script for cw-telegram-agent
# Handles environment setup, logging, and process management

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_PATH="$PROJECT_ROOT/venv"
MAIN_SCRIPT="$PROJECT_ROOT/run.py"
PID_FILE="$PROJECT_ROOT/tmp/run.pid"
LOG_DIR="$PROJECT_ROOT/tmp"
LOG_FILE="$LOG_DIR/run.log"
LOG_BASE_NAME="run"
ENV_FILE="$PROJECT_ROOT/.env"
SERVICE_NAME="Agent Server"

# Source the shared library
source "$SCRIPT_DIR/scripts/lib.sh"

# Start the agent server
start_server() {
    log_info "Starting $SERVICE_NAME..."
    log_info "Log file: $LOG_FILE"
    log_info "PID file: $PID_FILE"

    # Setup environment
    setup_environment

    # Rotate logs and clean cache
    rotate_logs
    clean_cache

    # Start the server completely detached
    cd "$PROJECT_ROOT"
    source "$VENV_PATH/bin/activate"
    if [ -f "$ENV_FILE" ]; then
        source "$ENV_FILE"
    fi
    export PYTHONPATH="$PROJECT_ROOT/src:$PYTHONPATH"

    python "$MAIN_SCRIPT" \
        < /dev/null \
        > "$LOG_FILE" 2>&1 &

    # Use the shared core startup logic
    start_server_core 2
}

# Show help
show_help() {
    cat << EOF
Agent Server Management Script

Usage: $0 {start|stop|restart|status|logs|recent} [OPTIONS]

Commands:
    start                   Start the agent server
    stop                    Stop the agent server gracefully
    restart                 Restart the agent server
    status                  Show server status and recent log entries
    logs                    Show live log output (Ctrl+C to exit)
    recent                  Show last 50 lines of logs

Examples:
    $0 start                    # Start the agent server
    $0 stop                     # Stop the server
    $0 restart                  # Restart the server
    $0 status                   # Check if running and show recent logs
    $0 logs                     # View live logs
    $0 recent                   # View recent log entries

Environment Variables:
    CINDY_AGENT_CONFIG_PATH     Media directories to scan (colon-separated)
    CINDY_AGENT_STATE_DIR       State directory for Telegram sessions
    GOOGLE_GEMINI_API_KEY       API key for AI descriptions
    TELEGRAM_API_ID             Telegram API ID
    TELEGRAM_API_HASH           Telegram API Hash

Files:
    PID file: $PID_FILE
    Log file: $LOG_FILE
    Log directory: $LOG_DIR
    Environment file: $ENV_FILE

Log Rotation:
    The script automatically rotates log files, keeping the 5 most recent:
    - run.log (current)
    - run1.log (previous)
    - run2.log (2 runs ago)
    - run3.log (3 runs ago)
    - run4.log (4 runs ago)
    - run5.log (5 runs ago)

EOF
}

# Run main function with all arguments
main "$@"
