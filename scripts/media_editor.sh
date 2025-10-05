#!/bin/bash

# Media Editor Startup Script for cw-telegram-agent
# Handles environment setup, logging, and process management

# Common configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Service-specific configuration
MAIN_SCRIPT="$PROJECT_ROOT/src/media_editor.py"
PID_FILE="$PROJECT_ROOT/tmp/media_editor.pid"
LOG_BASE_NAME="media_editor"
DEFAULT_PORT=5001
DEFAULT_HOST="0.0.0.0"
SERVICE_NAME="Media Editor"

# Source the shared library
source "$SCRIPT_DIR/lib.sh"

# Set LOG_FILE after sourcing library (depends on LOG_DIR)
LOG_FILE="$LOG_DIR/media_editor.log"

# Callback functions for the shared library

# Startup command: run the media editor with port/host arguments
startup_command() {
    local port=${1:-$DEFAULT_PORT}
    local host=${2:-$DEFAULT_HOST}

    log_info "Port: $port"
    log_info "Host: $host"

    cd "$PROJECT_ROOT"
    source "$VENV_PATH/bin/activate"
    export PYTHONPATH="$PROJECT_ROOT/src:$PYTHONPATH"

    python "$MAIN_SCRIPT" \
        --port "$port" \
        --host "$host" \
        < /dev/null \
        > "$LOG_FILE" 2>&1 &
}

# Post-startup hook: display URL
post_startup_hook() {
    local port=${1:-$DEFAULT_PORT}
    local host=${2:-$DEFAULT_HOST}
    log_info "URL: http://$host:$port"
}

# Custom help function
custom_help() {
    cat << EOF
Media Editor Management Script

Usage: $0 {start|stop|restart|status|logs} [OPTIONS]

Commands:
    start [port] [host]    Start the media editor server
                          (default: port=$DEFAULT_PORT, host=$DEFAULT_HOST)
    stop                   Stop the media editor server gracefully
    restart [port] [host]  Restart the media editor server
    status                 Show server status
    logs                   Show live log output (Ctrl+C to exit)

Examples:
    $0 start                    # Start on default port 5001
    $0 start 8080               # Start on port 8080
    $0 start 8080 127.0.0.1     # Start on port 8080, localhost only
    $0 stop                     # Stop the server
    $0 restart 5001             # Restart on port 5001
    $0 status                   # Check if running
    $0 logs                     # View live logs

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

Log Rotation:
    The script automatically rotates log files, keeping the 5 most recent:
    - media_editor.log (current)
    - media_editor1.log (previous)
    - media_editor2.log (2 runs ago)
    - media_editor3.log (3 runs ago)
    - media_editor4.log (4 runs ago)
    - media_editor5.log (5 runs ago)

EOF
}

# Run main function with all arguments
main "$@"
