#!/bin/bash

# Media Editor Startup Script for cw-telegram-agent
# Handles environment setup, logging, and process management

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_PATH="$PROJECT_ROOT/venv"
MAIN_SCRIPT="$PROJECT_ROOT/media_editor.py"
PID_FILE="$PROJECT_ROOT/tmp/media_editor.pid"
LOG_DIR="$PROJECT_ROOT/tmp"
LOG_FILE="$LOG_DIR/media_editor.log"
LOG_BASE_NAME="media_editor"
DEFAULT_PORT=5001
DEFAULT_HOST="0.0.0.0"
SERVICE_NAME="Media Editor"
# Note: ENV_FILE is not set, so check_env() will be skipped

# Source the shared library
source "$SCRIPT_DIR/scripts/lib.sh"

# Start the media editor
start_server() {
    local port=${1:-$DEFAULT_PORT}
    local host=${2:-$DEFAULT_HOST}

    log_info "Starting $SERVICE_NAME..."
    log_info "Port: $port"
    log_info "Host: $host"
    log_info "Log file: $LOG_FILE"

    # Setup environment
    setup_environment

    # Rotate logs
    rotate_logs

    # Start the server completely detached
    cd "$PROJECT_ROOT"
    source "$VENV_PATH/bin/activate"
    export PYTHONPATH="$PROJECT_ROOT/src:$PYTHONPATH"

    python "$MAIN_SCRIPT" \
        --port "$port" \
        --host "$host" \
        < /dev/null \
        > "$LOG_FILE" 2>&1 &

    # Use the shared core startup logic
    start_server_core 1

    # Add URL info for media editor
    log_info "URL: http://$host:$port"
}

# Show help
show_help() {
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
