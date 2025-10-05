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
ENV_FILE="$PROJECT_ROOT/.env"

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

# Check if main script exists
check_script() {
    if [ ! -f "$MAIN_SCRIPT" ]; then
        log_error "Main script not found at $MAIN_SCRIPT"
        exit 1
    fi
}

# Check if .env file exists
check_env() {
    if [ ! -f "$ENV_FILE" ]; then
        log_warning ".env file not found at $ENV_FILE"
        log_info "Make sure to create it with your environment variables"
    fi
}

# Check if server is already running
check_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            log_warning "Agent server is already running with PID $PID"
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
        [ -f "$LOG_DIR/run4.log" ] && mv "$LOG_DIR/run4.log" "$LOG_DIR/run5.log" 2>/dev/null || true
        [ -f "$LOG_DIR/run3.log" ] && mv "$LOG_DIR/run3.log" "$LOG_DIR/run4.log" 2>/dev/null || true
        [ -f "$LOG_DIR/run2.log" ] && mv "$LOG_DIR/run2.log" "$LOG_DIR/run3.log" 2>/dev/null || true
        [ -f "$LOG_DIR/run1.log" ] && mv "$LOG_DIR/run1.log" "$LOG_DIR/run2.log" 2>/dev/null || true
        [ -f "$LOG_DIR/run.log" ] && mv "$LOG_DIR/run.log" "$LOG_DIR/run1.log" 2>/dev/null || true
    fi
}

# Clean up Python cache files
clean_cache() {
    log_info "Cleaning up Python cache files..."
    find "$PROJECT_ROOT" -name "*.pyc" -delete 2>/dev/null || true
    find "$PROJECT_ROOT" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
}

# Start the agent server
start_server() {
    log_info "Starting Agent Server..."
    log_info "Log file: $LOG_FILE"
    log_info "PID file: $PID_FILE"

    # Create log directory if it doesn't exist
    mkdir -p "$LOG_DIR"

    # Rotate logs and clean cache
    rotate_logs
    clean_cache

    # Source the virtual environment
    source "$VENV_PATH/bin/activate"

    # Source environment variables if .env exists
    if [ -f "$ENV_FILE" ]; then
        source "$ENV_FILE"
    fi

    # Set up environment variables
    export PYTHONPATH="$PROJECT_ROOT/src:$PYTHONPATH"

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

    local server_pid=$!
    disown $server_pid  # Detach the server process from the parent shell
    echo $server_pid > "$PID_FILE"

    # Wait a moment for the server to start
    sleep 2

    # Check if the server is still running
    if ps -p "$server_pid" > /dev/null 2>&1; then
        log_success "Agent Server started successfully!"
        log_info "PID: $server_pid"
        log_info "Log file: $LOG_FILE"
        log_info "PID file: $PID_FILE"
        log_info "Note: Server may take a few seconds to be fully ready"
        echo "$server_pid"
    else
        log_error "Failed to start Agent Server"
        log_error "Check the log file for details: $LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# Stop the agent server
stop_server() {
    if [ ! -f "$PID_FILE" ]; then
        log_warning "No PID file found. Agent Server may not be running."
        return 0
    fi

    local pid=$(cat "$PID_FILE")
    if ! ps -p "$pid" > /dev/null 2>&1; then
        log_warning "Process $pid not found. Removing stale PID file."
        rm -f "$PID_FILE"
        return 0
    fi

    log_info "Stopping Agent Server (PID: $pid)..."

    # Use TERM signal for graceful shutdown
    if kill -TERM "$pid" 2>/dev/null; then
        # Wait for graceful shutdown
        local count=0
        while ps -p "$pid" > /dev/null 2>&1 && [ $count -lt 30 ]; do
            sleep 1
            count=$((count + 1))
        done

        # Force kill if still running
        if ps -p "$pid" > /dev/null 2>&1; then
            log_warning "Process still running after TERM, force killing..."
            kill -KILL "$pid" 2>/dev/null || true
            sleep 1
        fi

        rm -f "$PID_FILE"
        log_success "Agent Server stopped"
    else
        log_error "Failed to stop Agent Server (PID: $pid)"
        exit 1
    fi
}

# Restart the agent server
restart_server() {
    log_info "Restarting Agent Server..."
    stop_server
    sleep 1
    start_server
}

# Show status
show_status() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            log_success "Agent Server is running (PID: $pid)"
            log_info "PID file: $PID_FILE"
            log_info "Log file: $LOG_FILE"
            # Show recent log entries
            if [ -f "$LOG_FILE" ]; then
                log_info "Recent log entries:"
                tail -5 "$LOG_FILE" | sed 's/^/  /'
            fi
        else
            log_warning "PID file exists but process not running (stale PID file)"
            rm -f "$PID_FILE"
        fi
    else
        log_info "Agent Server is not running"
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

# Show recent logs (last 50 lines)
show_recent_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -50 "$LOG_FILE"
    else
        log_warning "Log file not found: $LOG_FILE"
    fi
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

# Main script logic
main() {
    cd "$PROJECT_ROOT"

    case "${1:-help}" in
        start)
            check_venv
            check_script
            check_env
            if ! check_running; then
                start_server
            fi
            ;;
        stop)
            stop_server
            ;;
        restart)
            check_venv
            check_script
            check_env
            restart_server
            ;;
        status)
            show_status
            ;;
        logs)
            show_logs
            ;;
        recent)
            show_recent_logs
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
