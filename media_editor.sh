#!/bin/bash

# Media Editor Startup Script for cw-telegram-agent
# Handles environment setup, logging, and process management

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"
VENV_PATH="$PROJECT_ROOT/venv"
MEDIA_EDITOR_SCRIPT="$PROJECT_ROOT/media_editor.py"
PID_FILE="$PROJECT_ROOT/tmp/media_editor.pid"
LOG_DIR="$PROJECT_ROOT/tmp"
LOG_FILE="$LOG_DIR/media_editor.log"
DEFAULT_PORT=5001
DEFAULT_HOST="0.0.0.0"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if virtual environment exists
check_venv() {
    if [ ! -d "$VENV_PATH" ]; then
        log_error "Virtual environment not found at $VENV_PATH"
        log_info "Please create it with: python3.13 -m venv venv"
        exit 1
    fi
}

# Check if media editor script exists
check_script() {
    if [ ! -f "$MEDIA_EDITOR_SCRIPT" ]; then
        log_error "Media editor script not found at $MEDIA_EDITOR_SCRIPT"
        exit 1
    fi
}

# Check if server is already running
check_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            log_warning "Media editor is already running with PID $PID"
            log_info "Use '$0 stop' to stop it first, or '$0 restart' to restart"
            return 0
        else
            log_info "Stale PID file found, removing it"
            rm -f "$PID_FILE"
        fi
    fi
    return 1
}

# Rotate log files (keep 5 most recent)
rotate_logs() {
    if [ -d "$LOG_DIR" ]; then
        # Rotate existing log files
        [ -f "$LOG_DIR/media_editor4.log" ] && mv "$LOG_DIR/media_editor4.log" "$LOG_DIR/media_editor5.log" 2>/dev/null || true
        [ -f "$LOG_DIR/media_editor3.log" ] && mv "$LOG_DIR/media_editor3.log" "$LOG_DIR/media_editor4.log" 2>/dev/null || true
        [ -f "$LOG_DIR/media_editor2.log" ] && mv "$LOG_DIR/media_editor2.log" "$LOG_DIR/media_editor3.log" 2>/dev/null || true
        [ -f "$LOG_DIR/media_editor1.log" ] && mv "$LOG_DIR/media_editor1.log" "$LOG_DIR/media_editor2.log" 2>/dev/null || true
        [ -f "$LOG_DIR/media_editor.log" ] && mv "$LOG_DIR/media_editor.log" "$LOG_DIR/media_editor1.log" 2>/dev/null || true
    fi
}

# Start the media editor
start_server() {
    local port=${1:-$DEFAULT_PORT}
    local host=${2:-$DEFAULT_HOST}

    log_info "Starting Media Editor..."
    log_info "Port: $port"
    log_info "Host: $host"
    log_info "Log file: $LOG_FILE"

    # Source the virtual environment
    source "$VENV_PATH/bin/activate"

    # Set up environment variables if not already set
    export PYTHONPATH="$PROJECT_ROOT/src:$PYTHONPATH"

    # Create log directory if it doesn't exist
    mkdir -p "$LOG_DIR"

    # Rotate logs
    rotate_logs

    # Start the server completely detached (like start_media_editor.sh)
    cd "$PROJECT_ROOT"
    source "$VENV_PATH/bin/activate"
    export PYTHONPATH="$PROJECT_ROOT/src:$PYTHONPATH"

    python "$MEDIA_EDITOR_SCRIPT" \
        --port "$port" \
        --host "$host" \
        < /dev/null \
        > "$LOG_FILE" 2>&1 &

    local server_pid=$!
    disown $server_pid  # Detach the server process from the parent shell
    echo $server_pid > "$PID_FILE"

    # Wait a moment for the server to start
    sleep 1

    # Check if the server is still running
    if ps -p "$server_pid" > /dev/null 2>&1; then
        log_success "Media Editor started successfully!"
        log_info "PID: $server_pid"
        log_info "URL: http://$host:$port"
        log_info "Log file: $LOG_FILE"
        log_info "PID file: $PID_FILE"
        log_info "Note: Server may take a few seconds to be fully ready"
        echo "$server_pid"
    else
        log_error "Failed to start Media Editor"
        log_error "Check the log file for details: $LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# Stop the media editor
stop_server() {
    if [ ! -f "$PID_FILE" ]; then
        log_warning "No PID file found. Media Editor may not be running."
        return 0
    fi

    local pid=$(cat "$PID_FILE")
    if ! ps -p "$pid" > /dev/null 2>&1; then
        log_warning "Process $pid not found. Removing stale PID file."
        rm -f "$PID_FILE"
        return 0
    fi

    log_info "Stopping Media Editor (PID: $pid)..."

    # Use HUP signal to allow graceful shutdown of Telegram connections
    if kill -HUP "$pid" 2>/dev/null; then
        # Wait for graceful shutdown
        local count=0
        while ps -p "$pid" > /dev/null 2>&1 && [ $count -lt 30 ]; do
            sleep 1
            count=$((count + 1))
        done

        # Force kill if still running
        if ps -p "$pid" > /dev/null 2>&1; then
            log_warning "Process still running after HUP, force killing..."
            kill -TERM "$pid" 2>/dev/null || true
            sleep 2
            if ps -p "$pid" > /dev/null 2>&1; then
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi

        rm -f "$PID_FILE"
        log_success "Media Editor stopped"
    else
        log_error "Failed to stop Media Editor (PID: $pid)"
        exit 1
    fi
}

# Restart the media editor
restart_server() {
    local port=${1:-$DEFAULT_PORT}
    local host=${2:-$DEFAULT_HOST}

    log_info "Restarting Media Editor..."
    stop_server
    sleep 1
    start_server "$port" "$host"
}

# Show status
show_status() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            log_success "Media Editor is running (PID: $pid)"
            log_info "PID file: $PID_FILE"
            log_info "Log file: $LOG_FILE"
            # Try to get the port from the log file
            if [ -f "$LOG_FILE" ]; then
                local port=$(grep "Starting Media Editor on" "$LOG_FILE" | tail -1 | sed 's/.*:\([0-9]*\).*/\1/')
                if [ -n "$port" ]; then
                    log_info "URL: http://$DEFAULT_HOST:$port"
                fi
            fi
        else
            log_warning "PID file exists but process not running (stale PID file)"
            rm -f "$PID_FILE"
        fi
    else
        log_info "Media Editor is not running"
    fi
}

# Show logs
show_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        log_warning "Log file not found: $LOG_FILE"
    fi
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
    $0 start                    # Start on default port 5000
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

# Main script logic
main() {
    cd "$PROJECT_ROOT"

    case "${1:-help}" in
        start)
            check_venv
            check_script
            if ! check_running; then
                start_server "$2" "$3"
            fi
            ;;
        stop)
            stop_server
            ;;
        restart)
            check_venv
            check_script
            restart_server "$2" "$3"
            ;;
        status)
            show_status
            ;;
        logs)
            show_logs
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "Unknown command: $1"
            echo
            show_help
            exit 1
            ;;
    esac
}

# Run main function with all arguments
main "$@"
