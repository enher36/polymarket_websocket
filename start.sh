#!/bin/bash
#
# Polymarket Real-time Data Fetcher - Interactive Setup & Launch
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ==================== Colors ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# Status symbols
CHECK="${GREEN}✓${NC}"
CROSS="${RED}✗${NC}"
WARN="${YELLOW}!${NC}"
ARROW="${CYAN}→${NC}"

# ==================== Port Configuration ====================
get_configured_ports() {
    # Default values
    WEB_PORT=8080
    FORWARD_PORT=8765

    # Read from .env if exists
    if [ -f ".env" ]; then
        local web_port_line=$(grep "^POLYMARKET_WEB_PORT=" .env 2>/dev/null)
        local forward_port_line=$(grep "^POLYMARKET_FORWARD_PORT=" .env 2>/dev/null)

        if [ -n "$web_port_line" ]; then
            WEB_PORT=$(echo "$web_port_line" | cut -d'=' -f2)
        fi
        if [ -n "$forward_port_line" ]; then
            FORWARD_PORT=$(echo "$forward_port_line" | cut -d'=' -f2)
        fi
    fi
}

# Load ports on script start
get_configured_ports

# ==================== Utilities ====================
print_banner() {
    clear
    echo -e "${CYAN}"
    echo "  ╔═══════════════════════════════════════════════════════════╗"
    echo "  ║                                                           ║"
    echo "  ║   ${BOLD}Polymarket Real-time Data Fetcher${NC}${CYAN}                      ║"
    echo "  ║                                                           ║"
    echo "  ║   ${DIM}WebSocket streaming • REST API • SQLite storage${NC}${CYAN}        ║"
    echo "  ║                                                           ║"
    echo "  ╚═══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_section() {
    echo -e "\n${BOLD}${BLUE}━━━ $1 ━━━${NC}\n"
}

print_info() {
    echo -e "  ${CHECK} $1"
}

print_warn() {
    echo -e "  ${WARN} ${YELLOW}$1${NC}"
}

print_error() {
    echo -e "  ${CROSS} ${RED}$1${NC}"
}

print_status() {
    local name="$1"
    local status="$2"
    local detail="$3"

    if [ "$status" = "ok" ]; then
        printf "  ${CHECK} %-20s ${DIM}%s${NC}\n" "$name" "$detail"
    elif [ "$status" = "warn" ]; then
        printf "  ${WARN} %-20s ${YELLOW}%s${NC}\n" "$name" "$detail"
    else
        printf "  ${CROSS} %-20s ${RED}%s${NC}\n" "$name" "$detail"
    fi
}

# ==================== Environment Detection ====================
ENV_PYTHON=""
ENV_PYTHON_VER=""
ENV_UV=""
ENV_UV_VER=""
ENV_VENV=""
ENV_DEPS=""
ENV_DB=""
ENV_RUNNING=""
ENV_READY=""

detect_environment() {
    # Python
    if command -v python3 &> /dev/null; then
        ENV_PYTHON="python3"
        ENV_PYTHON_VER=$(python3 --version 2>&1 | cut -d' ' -f2)
    elif command -v python &> /dev/null; then
        ENV_PYTHON="python"
        ENV_PYTHON_VER=$(python --version 2>&1 | cut -d' ' -f2)
    fi

    # uv package manager
    if command -v uv &> /dev/null; then
        ENV_UV="installed"
        ENV_UV_VER=$(uv --version 2>&1 | head -1 | cut -d' ' -f2)
    fi

    # Virtual environment
    if [ -d ".venv" ] && [ -f ".venv/bin/activate" ]; then
        ENV_VENV="exists"
    fi

    # Dependencies installed
    if [ -f ".venv/bin/python" ]; then
        if .venv/bin/python -c "import polymarket_realtime" 2>/dev/null; then
            ENV_DEPS="installed"
        fi
    fi

    # Database
    if [ -f "polymarket.db" ]; then
        local db_size=$(du -h polymarket.db 2>/dev/null | cut -f1)
        ENV_DB="$db_size"
    fi

    # Check if already running
    if [ -f "polymarket.pid" ]; then
        local pid=$(cat polymarket.pid)
        if kill -0 "$pid" 2>/dev/null; then
            ENV_RUNNING="$pid"
        fi
    fi

    # Overall readiness
    if [ -n "$ENV_PYTHON" ] && [ "$ENV_VENV" = "exists" ] && [ "$ENV_DEPS" = "installed" ]; then
        ENV_READY="yes"
    fi
}

show_environment_status() {
    print_section "Environment Status"

    # Python
    if [ -n "$ENV_PYTHON" ]; then
        print_status "Python" "ok" "v$ENV_PYTHON_VER"
    else
        print_status "Python" "fail" "Not found"
    fi

    # uv
    if [ -n "$ENV_UV" ]; then
        print_status "uv (pkg manager)" "ok" "v$ENV_UV_VER"
    else
        print_status "uv (pkg manager)" "fail" "Not installed"
    fi

    # Virtual environment
    if [ "$ENV_VENV" = "exists" ]; then
        print_status "Virtual env" "ok" ".venv/"
    else
        print_status "Virtual env" "warn" "Not created"
    fi

    # Dependencies
    if [ "$ENV_DEPS" = "installed" ]; then
        print_status "Dependencies" "ok" "All installed"
    else
        print_status "Dependencies" "warn" "Not installed"
    fi

    # Database
    if [ -n "$ENV_DB" ]; then
        print_status "Database" "ok" "polymarket.db ($ENV_DB)"
    else
        print_status "Database" "warn" "Will be created on first run"
    fi

    # Running status
    if [ -n "$ENV_RUNNING" ]; then
        print_status "Status" "ok" "Running (PID: $ENV_RUNNING)"
    else
        print_status "Status" "warn" "Not running"
    fi
}

# ==================== Installation ====================
install_uv() {
    echo -e "\n  ${ARROW} Installing uv package manager..."
    if curl -LsSf https://astral.sh/uv/install.sh | sh; then
        # Reload PATH
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        if command -v uv &> /dev/null; then
            print_info "uv installed successfully"
            ENV_UV="installed"
            ENV_UV_VER=$(uv --version 2>&1 | head -1 | cut -d' ' -f2)
            return 0
        fi
    fi
    print_error "Failed to install uv"
    return 1
}

setup_environment() {
    print_section "Setting Up Environment"

    # Check Python
    if [ -z "$ENV_PYTHON" ]; then
        print_error "Python 3.10+ is required but not found"
        echo -e "\n  Please install Python first:"
        echo "    Ubuntu/Debian: sudo apt install python3"
        echo "    macOS: brew install python3"
        return 1
    fi

    # Install uv if needed
    if [ -z "$ENV_UV" ]; then
        echo -e "  ${WARN} uv package manager not found"
        read -p "  Install uv automatically? [Y/n] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            install_uv || return 1
        else
            print_error "uv is required to continue"
            return 1
        fi
    else
        print_info "uv is available"
    fi

    # Create virtual environment
    if [ "$ENV_VENV" != "exists" ]; then
        echo -e "  ${ARROW} Creating virtual environment..."
        uv venv .venv
        print_info "Virtual environment created"
        ENV_VENV="exists"
    else
        print_info "Virtual environment exists"
    fi

    # Activate and install dependencies
    source .venv/bin/activate

    if [ "$ENV_DEPS" != "installed" ]; then
        echo -e "  ${ARROW} Installing dependencies..."
        uv pip install -e . 2>&1 | while read line; do
            echo -ne "\r  ${DIM}Installing... ${NC}"
        done
        echo -ne "\r"

        # Verify installation
        if .venv/bin/python -c "import polymarket_realtime" 2>/dev/null; then
            print_info "Dependencies installed"
            ENV_DEPS="installed"
        else
            print_error "Failed to install dependencies"
            return 1
        fi
    else
        print_info "Dependencies already installed"
    fi

    # Create .env if needed
    if [ ! -f ".env" ] && [ -f ".env.example" ]; then
        cp .env.example .env
        print_info "Created .env from template"
    fi

    ENV_READY="yes"

    echo -e "\n  ${CHECK} ${GREEN}Setup complete!${NC}"
    return 0
}

# ==================== Application Control ====================
start_foreground() {
    if [ "$ENV_READY" != "yes" ]; then
        print_error "Environment not ready. Please run setup first."
        return 1
    fi

    if [ -n "$ENV_RUNNING" ]; then
        print_warn "Application already running (PID: $ENV_RUNNING)"
        return 1
    fi

    source .venv/bin/activate

    # Get local IP
    local LOCAL_IP=""
    if command -v hostname &> /dev/null; then
        LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    fi

    print_section "Starting Application"
    echo -e "  ${ARROW} Web Dashboard:   ${CYAN}http://0.0.0.0:${WEB_PORT}${NC}"
    if [ -n "$LOCAL_IP" ]; then
        echo -e "  ${ARROW} Network Access:  ${CYAN}http://$LOCAL_IP:${WEB_PORT}${NC}"
    fi
    echo -e "  ${ARROW} Forward Server:  ${CYAN}ws://0.0.0.0:${FORWARD_PORT}${NC}"
    echo ""
    echo -e "  ${DIM}Press Ctrl+C to stop${NC}"
    echo ""

    python -m polymarket_realtime.main
}

start_background() {
    if [ "$ENV_READY" != "yes" ]; then
        print_error "Environment not ready. Please run setup first."
        return 1
    fi

    if [ -n "$ENV_RUNNING" ]; then
        print_warn "Application already running (PID: $ENV_RUNNING)"
        return 1
    fi

    source .venv/bin/activate

    local LOG_FILE="polymarket.log"
    local PID_FILE="polymarket.pid"

    echo -e "  ${ARROW} Starting in background..."
    nohup python -m polymarket_realtime.main > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"

    sleep 1

    if kill -0 "$(cat $PID_FILE)" 2>/dev/null; then
        ENV_RUNNING=$(cat $PID_FILE)
        # Get local IP
        local LOCAL_IP=""
        if command -v hostname &> /dev/null; then
            LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
        fi

        print_info "Started with PID: $ENV_RUNNING"
        echo ""
        echo -e "  ${ARROW} Log file:        ${CYAN}$LOG_FILE${NC}"
        echo -e "  ${ARROW} Web Dashboard:   ${CYAN}http://0.0.0.0:${WEB_PORT}${NC}"
        if [ -n "$LOCAL_IP" ]; then
            echo -e "  ${ARROW} Network Access:  ${CYAN}http://$LOCAL_IP:${WEB_PORT}${NC}"
        fi
        echo -e "  ${ARROW} Forward Server:  ${CYAN}ws://0.0.0.0:${FORWARD_PORT}${NC}"
        echo ""
        echo -e "  ${DIM}View logs: tail -f $LOG_FILE${NC}"
    else
        print_error "Failed to start application"
        rm -f "$PID_FILE"
        return 1
    fi
}

stop_application() {
    local PID_FILE="polymarket.pid"

    if [ -z "$ENV_RUNNING" ]; then
        print_warn "Application is not running"
        return 0
    fi

    echo -e "  ${ARROW} Stopping process (PID: $ENV_RUNNING)..."
    kill "$ENV_RUNNING" 2>/dev/null

    # Wait for graceful shutdown
    local count=0
    while kill -0 "$ENV_RUNNING" 2>/dev/null && [ $count -lt 10 ]; do
        sleep 0.5
        count=$((count + 1))
    done

    if kill -0 "$ENV_RUNNING" 2>/dev/null; then
        echo -e "  ${ARROW} Force killing..."
        kill -9 "$ENV_RUNNING" 2>/dev/null
    fi

    rm -f "$PID_FILE"
    ENV_RUNNING=""
    print_info "Application stopped"
}

show_logs() {
    local LOG_FILE="polymarket.log"

    if [ ! -f "$LOG_FILE" ]; then
        print_warn "No log file found"
        return 1
    fi

    echo -e "\n  ${DIM}Showing last 20 lines (Ctrl+C to exit live view)${NC}\n"
    tail -20 "$LOG_FILE"
    echo ""
    read -p "  View live logs? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        tail -f "$LOG_FILE"
    fi
}

# ==================== Interactive Menu ====================
show_menu() {
    echo -e "\n${BOLD}Actions:${NC}\n"

    if [ "$ENV_READY" != "yes" ]; then
        echo -e "  ${CYAN}1${NC}) Setup environment (required)"
    else
        echo -e "  ${DIM}1) Setup environment (completed)${NC}"
    fi

    if [ -n "$ENV_RUNNING" ]; then
        echo -e "  ${DIM}2) Start application (already running)${NC}"
        echo -e "  ${DIM}3) Start in background (already running)${NC}"
        echo -e "  ${CYAN}4${NC}) Stop application"
        echo -e "  ${CYAN}5${NC}) View logs"
    else
        if [ "$ENV_READY" = "yes" ]; then
            echo -e "  ${CYAN}2${NC}) Start application"
            echo -e "  ${CYAN}3${NC}) Start in background"
        else
            echo -e "  ${DIM}2) Start application (setup required)${NC}"
            echo -e "  ${DIM}3) Start in background (setup required)${NC}"
        fi
        echo -e "  ${DIM}4) Stop application (not running)${NC}"
        if [ -f "polymarket.log" ]; then
            echo -e "  ${CYAN}5${NC}) View logs"
        else
            echo -e "  ${DIM}5) View logs (no logs yet)${NC}"
        fi
    fi

    echo ""
    echo -e "  ${CYAN}q${NC}) Quit"
    echo ""
}

handle_menu_choice() {
    read -p "  Select option: " -n 1 -r choice
    echo ""

    case $choice in
        1)
            setup_environment
            ;;
        2)
            if [ -n "$ENV_RUNNING" ]; then
                print_warn "Application already running"
            elif [ "$ENV_READY" != "yes" ]; then
                print_error "Setup required first"
            else
                start_foreground
                exit 0
            fi
            ;;
        3)
            if [ -n "$ENV_RUNNING" ]; then
                print_warn "Application already running"
            elif [ "$ENV_READY" != "yes" ]; then
                print_error "Setup required first"
            else
                start_background
            fi
            ;;
        4)
            stop_application
            ;;
        5)
            if [ -f "polymarket.log" ]; then
                show_logs
            else
                print_warn "No logs available"
            fi
            ;;
        q|Q)
            echo -e "\n  ${DIM}Goodbye!${NC}\n"
            exit 0
            ;;
        *)
            print_warn "Invalid option"
            ;;
    esac
}

# ==================== Main ====================
main_interactive() {
    while true; do
        print_banner
        detect_environment
        show_environment_status
        show_menu
        handle_menu_choice

        echo ""
        read -p "  Press Enter to continue..." -r
    done
}

# Handle command line arguments for non-interactive use
case "${1:-}" in
    --setup)
        print_banner
        detect_environment
        setup_environment
        ;;
    --start)
        detect_environment
        start_foreground
        ;;
    --bg)
        detect_environment
        start_background
        ;;
    --stop)
        detect_environment
        stop_application
        ;;
    --status)
        print_banner
        detect_environment
        show_environment_status
        ;;
    --help|-h)
        echo "Polymarket Real-time Data Fetcher"
        echo ""
        echo "Usage:"
        echo "  ./start.sh          Interactive menu (recommended)"
        echo "  ./start.sh --setup  Setup environment"
        echo "  ./start.sh --start  Start in foreground"
        echo "  ./start.sh --bg     Start in background"
        echo "  ./start.sh --stop   Stop background process"
        echo "  ./start.sh --status Show environment status"
        echo "  ./start.sh --help   Show this help"
        ;;
    *)
        main_interactive
        ;;
esac
