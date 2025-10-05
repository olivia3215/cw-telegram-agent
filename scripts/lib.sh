#!/bin/bash

# Shared Library for Service Management Scripts
# Common functions for run.sh and media_editor.sh
# Usage: source "$(dirname "$0")/scripts/lib.sh"

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

# Check if .env file exists (optional)
check_env() {
    if [ -n "$ENV_FILE" ] && [ ! -f "$ENV_FILE" ]; then
        log_warning ".env file not found at $ENV_FILE"
        log_info "Make sure to create it with your environment variables"
    fi
}

# Check if server is already running
check_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if ps -p "$PID" > /dev/null 2>&1; then
            log_warning "$SERVICE_NAME is already running with PID $PID"
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
        [ -f "$LOG_DIR/${LOG_BASE_NAME}4.log" ] && mv "$LOG_DIR/${LOG_BASE_NAME}4.log" "$LOG_DIR/${LOG_BASE_NAME}5.log" 2>/dev/null || true
        [ -f "$LOG_DIR/${LOG_BASE_NAME}3.log" ] && mv "$LOG_DIR/${LOG_BASE_NAME}3.log" "$LOG_DIR/${LOG_BASE_NAME}4.log" 2>/dev/null || true
        [ -f "$LOG_DIR/${LOG_BASE_NAME}2.log" ] && mv "$LOG_DIR/${LOG_BASE_NAME}2.log" "$LOG_DIR/${LOG_BASE_NAME}3.log" 2>/dev/null || true
        [ -f "$LOG_DIR/${LOG_BASE_NAME}1.log" ] && mv "$LOG_DIR/${LOG_BASE_NAME}1.log" "$LOG_DIR/${LOG_BASE_NAME}2.log" 2>/dev/null || true
        [ -f "$LOG_DIR/${LOG_BASE_NAME}.log" ] && mv "$LOG_DIR/${LOG_BASE_NAME}.log" "$LOG_DIR/${LOG_BASE_NAME}1.log" 2>/dev/null || true
    fi
}

# Clean up Python cache files
clean_cache() {
    log_info "Cleaning up Python cache files..."
    find "$PROJECT_ROOT" -name "*.pyc" -delete 2>/dev/null || true
    find "$PROJECT_ROOT" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
}

# Stop the server
stop_server() {
    if [ ! -f "$PID_FILE" ]; then
        log_warning "No PID file found. $SERVICE_NAME may not be running."
        return 0
    fi

    local pid=$(cat "$PID_FILE")
    if ! ps -p "$pid" > /dev/null 2>&1; then
        log_warning "Process $pid not found. Removing stale PID file."
        rm -f "$PID_FILE"
        return 0
    fi

    log_info "Stopping $SERVICE_NAME (PID: $pid)..."

    # Use appropriate signal for graceful shutdown
    local signal="TERM"
    if [ "$SERVICE_NAME" = "Media Editor" ]; then
        signal="HUP"  # Media editor uses HUP for graceful Telegram connection shutdown
    fi

    if kill -$signal "$pid" 2>/dev/null; then
        # Wait for graceful shutdown
        local count=0
        while ps -p "$pid" > /dev/null 2>&1 && [ $count -lt 30 ]; do
            sleep 1
            count=$((count + 1))
        done

        # Force kill if still running
        if ps -p "$pid" > /dev/null 2>&1; then
            log_warning "Process still running after $signal, force killing..."
            kill -TERM "$pid" 2>/dev/null || true
            sleep 2
            if ps -p "$pid" > /dev/null 2>&1; then
                kill -KILL "$pid" 2>/dev/null || true
            fi
        fi

        rm -f "$PID_FILE"
        log_success "$SERVICE_NAME stopped"
    else
        log_error "Failed to stop $SERVICE_NAME (PID: $pid)"
        exit 1
    fi
}

# Restart the server
restart_server() {
    log_info "Restarting $SERVICE_NAME..."
    stop_server
    sleep 1
    start_server "$@"
}

# Show status
show_status() {
    if [ -f "$PID_FILE" ]; then
        local pid=$(cat "$PID_FILE")
        if ps -p "$pid" > /dev/null 2>&1; then
            log_success "$SERVICE_NAME is running (PID: $pid)"
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
        log_info "$SERVICE_NAME is not running"
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

# Core server startup logic (common to all services)
start_server_core() {
    local server_pid=$!
    disown $server_pid  # Detach the server process from the parent shell
    echo $server_pid > "$PID_FILE"

    # Wait a moment for the server to start
    local wait_time=${1:-2}
    sleep $wait_time

    # Check if the server is still running
    if ps -p "$server_pid" > /dev/null 2>&1; then
        log_success "$SERVICE_NAME started successfully!"
        log_info "PID: $server_pid"
        log_info "Log file: $LOG_FILE"
        log_info "PID file: $PID_FILE"
        log_info "Note: Server may take a few seconds to be fully ready"
        echo "$server_pid"
    else
        log_error "Failed to start $SERVICE_NAME"
        log_error "Check the log file for details: $LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
}

# Generic start server function (to be customized by each script)
start_server() {
    log_error "start_server() function must be implemented by the calling script"
    exit 1
}

# Generic show help function (to be customized by each script)
show_help() {
    log_error "show_help() function must be implemented by the calling script"
    exit 1
}

# Setup common environment
setup_environment() {
    # Create log directory if it doesn't exist
    mkdir -p "$LOG_DIR"

    # Source the virtual environment
    source "$VENV_PATH/bin/activate"

    # Source environment variables if .env exists
    if [ -f "$ENV_FILE" ]; then
        source "$ENV_FILE"
    fi

    # Set up environment variables
    export PYTHONPATH="$PROJECT_ROOT/src:$PYTHONPATH"
}

# Pre-start validation hooks (can be overridden by scripts)
pre_start_checks() {
    check_venv
    check_script
    if [ -n "$ENV_FILE" ]; then
        check_env
    fi
}

# Pre-restart validation hooks (can be overridden by scripts)
pre_restart_checks() {
    check_venv
    check_script
    if [ -n "$ENV_FILE" ]; then
        check_env
    fi
}

# Main script logic dispatcher
main() {
    cd "$PROJECT_ROOT"

    case "${1:-help}" in
        start)
            pre_start_checks
            if ! check_running; then
                start_server "${@:2}"
            fi
            ;;
        stop)
            stop_server
            ;;
        restart)
            pre_restart_checks
            restart_server "${@:2}"
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
