#!/bin/bash
#
# Polymarket Real-time Data Fetcher - One-click Start Script
#
# Usage:
#   ./start.sh          # Start the application
#   ./start.sh --setup  # First-time setup (create venv and install deps)
#   ./start.sh --bg     # Run in background
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if uv is installed
check_uv() {
    if ! command -v uv &> /dev/null; then
        print_error "uv is not installed. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi
}

# Setup virtual environment and install dependencies
setup() {
    print_info "Setting up virtual environment..."
    check_uv

    if [ ! -d ".venv" ]; then
        uv venv .venv
        print_info "Virtual environment created"
    else
        print_warn "Virtual environment already exists"
    fi

    source .venv/bin/activate
    print_info "Installing dependencies..."
    uv pip install -e .

    # Create .env from example if not exists
    if [ ! -f ".env" ] && [ -f ".env.example" ]; then
        cp .env.example .env
        print_info "Created .env from .env.example"
    fi

    print_info "Setup complete!"
    echo ""
    echo "To start the application, run: ./start.sh"
}

# Start the application
start() {
    if [ ! -d ".venv" ]; then
        print_error "Virtual environment not found. Run: ./start.sh --setup"
        exit 1
    fi

    source .venv/bin/activate

    print_info "Starting Polymarket Real-time Fetcher..."
    echo ""
    echo "  Web Dashboard: http://127.0.0.1:8080"
    echo "  Forward Server: ws://0.0.0.0:8765"
    echo ""
    echo "Press Ctrl+C to stop"
    echo ""

    python -m polymarket_realtime.main
}

# Start in background
start_bg() {
    if [ ! -d ".venv" ]; then
        print_error "Virtual environment not found. Run: ./start.sh --setup"
        exit 1
    fi

    source .venv/bin/activate

    LOG_FILE="polymarket.log"
    PID_FILE="polymarket.pid"

    if [ -f "$PID_FILE" ]; then
        OLD_PID=$(cat "$PID_FILE")
        if kill -0 "$OLD_PID" 2>/dev/null; then
            print_warn "Application already running (PID: $OLD_PID)"
            echo "To stop it, run: kill $OLD_PID"
            exit 1
        fi
    fi

    print_info "Starting in background..."
    nohup python -m polymarket_realtime.main > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"

    print_info "Started with PID: $(cat $PID_FILE)"
    echo ""
    echo "  Log file: $LOG_FILE"
    echo "  PID file: $PID_FILE"
    echo "  Web Dashboard: http://127.0.0.1:8080"
    echo ""
    echo "To stop: kill \$(cat $PID_FILE)"
    echo "To view logs: tail -f $LOG_FILE"
}

# Stop background process
stop() {
    PID_FILE="polymarket.pid"

    if [ ! -f "$PID_FILE" ]; then
        print_warn "PID file not found"
        exit 0
    fi

    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        print_info "Stopping process (PID: $PID)..."
        kill "$PID"
        rm -f "$PID_FILE"
        print_info "Stopped"
    else
        print_warn "Process not running"
        rm -f "$PID_FILE"
    fi
}

# Main
case "${1:-}" in
    --setup)
        setup
        ;;
    --bg)
        start_bg
        ;;
    --stop)
        stop
        ;;
    --help|-h)
        echo "Polymarket Real-time Data Fetcher"
        echo ""
        echo "Usage:"
        echo "  ./start.sh          Start the application"
        echo "  ./start.sh --setup  First-time setup"
        echo "  ./start.sh --bg     Run in background"
        echo "  ./start.sh --stop   Stop background process"
        echo "  ./start.sh --help   Show this help"
        ;;
    *)
        start
        ;;
esac
