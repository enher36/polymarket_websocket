#!/bin/bash
#
# Polymarket Real-time Data Fetcher - One-line Installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/enher36/polymarket_websocket/main/install.sh | bash
#

set -e

# ==================== Colors ====================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

CHECK="${GREEN}✓${NC}"
CROSS="${RED}✗${NC}"
WARN="${YELLOW}!${NC}"
ARROW="${CYAN}→${NC}"

# ==================== Configuration ====================
REPO_URL="https://github.com/enher36/polymarket_websocket.git"
INSTALL_DIR="$HOME/polymarket_websocket"

# ==================== System Detection ====================
OS_TYPE=""
OS_ID=""
PKG_MANAGER=""
SUDO_CMD=""

detect_system() {
    # Detect OS type
    case "$(uname -s)" in
        Linux*)     OS_TYPE="linux";;
        Darwin*)    OS_TYPE="macos";;
        CYGWIN*|MINGW*|MSYS*) OS_TYPE="windows";;
        *)          OS_TYPE="unknown";;
    esac

    # Detect Linux distribution
    if [ "$OS_TYPE" = "linux" ]; then
        if [ -f /etc/os-release ]; then
            . /etc/os-release
            OS_ID="$ID"
        elif [ -f /etc/redhat-release ]; then
            OS_ID="rhel"
        elif [ -f /etc/debian_version ]; then
            OS_ID="debian"
        fi
    fi

    # Detect package manager
    if [ "$OS_TYPE" = "macos" ]; then
        if command -v brew &> /dev/null; then
            PKG_MANAGER="brew"
        fi
    elif [ "$OS_TYPE" = "linux" ]; then
        case "$OS_ID" in
            ubuntu|debian|linuxmint|pop)
                PKG_MANAGER="apt"
                ;;
            fedora|rhel|centos|rocky|alma)
                if command -v dnf &> /dev/null; then
                    PKG_MANAGER="dnf"
                else
                    PKG_MANAGER="yum"
                fi
                ;;
            arch|manjaro)
                PKG_MANAGER="pacman"
                ;;
            opensuse*|sles)
                PKG_MANAGER="zypper"
                ;;
            alpine)
                PKG_MANAGER="apk"
                ;;
        esac
    fi

    # Setup sudo if needed
    if [ "$(id -u)" != "0" ] && [ "$PKG_MANAGER" != "brew" ]; then
        if command -v sudo &> /dev/null; then
            SUDO_CMD="sudo"
        fi
    fi
}

# ==================== Functions ====================
print_banner() {
    echo -e "${CYAN}"
    echo "  ╔═══════════════════════════════════════════════════════════╗"
    echo "  ║                                                           ║"
    echo "  ║   ${BOLD}Polymarket Real-time Data Fetcher${NC}${CYAN}                      ║"
    echo "  ║                                                           ║"
    echo "  ║   ${DIM}One-line Installer${NC}${CYAN}                                      ║"
    echo "  ║                                                           ║"
    echo "  ╚═══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
}

print_step() {
    echo -e "\n${BOLD}${BLUE}[$1/$TOTAL_STEPS]${NC} $2\n"
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

check_command() {
    command -v "$1" &> /dev/null
}

# ==================== Package Installation ====================
install_package() {
    local pkg_name="$1"
    local friendly_name="${2:-$pkg_name}"

    echo -e "  ${ARROW} Installing $friendly_name..."

    case "$PKG_MANAGER" in
        apt)
            $SUDO_CMD apt-get update -qq
            $SUDO_CMD apt-get install -y -qq "$pkg_name"
            ;;
        dnf)
            $SUDO_CMD dnf install -y -q "$pkg_name"
            ;;
        yum)
            $SUDO_CMD yum install -y -q "$pkg_name"
            ;;
        pacman)
            $SUDO_CMD pacman -S --noconfirm --quiet "$pkg_name"
            ;;
        zypper)
            $SUDO_CMD zypper install -y -q "$pkg_name"
            ;;
        apk)
            $SUDO_CMD apk add --quiet "$pkg_name"
            ;;
        brew)
            brew install --quiet "$pkg_name"
            ;;
        *)
            return 1
            ;;
    esac
}

install_python() {
    echo -e "  ${ARROW} Installing Python 3..."

    case "$PKG_MANAGER" in
        apt)
            $SUDO_CMD apt-get update -qq
            $SUDO_CMD apt-get install -y -qq python3 python3-venv python3-pip
            ;;
        dnf|yum)
            $SUDO_CMD $PKG_MANAGER install -y -q python3 python3-pip
            ;;
        pacman)
            $SUDO_CMD pacman -S --noconfirm --quiet python python-pip
            ;;
        zypper)
            $SUDO_CMD zypper install -y -q python3 python3-pip
            ;;
        apk)
            $SUDO_CMD apk add --quiet python3 py3-pip
            ;;
        brew)
            brew install --quiet python3
            ;;
        *)
            return 1
            ;;
    esac
}

install_git() {
    install_package "git" "Git"
}

install_curl() {
    install_package "curl" "curl"
}

# ==================== Port Configuration ====================
WEB_PORT=8080
FORWARD_PORT=8765

configure_ports() {
    echo -e "\n${BOLD}Port Configuration${NC}\n"
    echo -e "  ${DIM}Press Enter to use default values${NC}\n"

    # Web Dashboard Port
    read -p "  Web Dashboard port [${WEB_PORT}]: " input_web_port
    if [ -n "$input_web_port" ]; then
        if [[ "$input_web_port" =~ ^[0-9]+$ ]] && [ "$input_web_port" -ge 1 ] && [ "$input_web_port" -le 65535 ]; then
            WEB_PORT="$input_web_port"
        else
            print_warn "Invalid port, using default: $WEB_PORT"
        fi
    fi

    # Forward Server Port
    read -p "  Forward Server port [${FORWARD_PORT}]: " input_forward_port
    if [ -n "$input_forward_port" ]; then
        if [[ "$input_forward_port" =~ ^[0-9]+$ ]] && [ "$input_forward_port" -ge 1 ] && [ "$input_forward_port" -le 65535 ]; then
            FORWARD_PORT="$input_forward_port"
        else
            print_warn "Invalid port, using default: $FORWARD_PORT"
        fi
    fi

    echo ""
    print_info "Web Dashboard: port $WEB_PORT"
    print_info "Forward Server: port $FORWARD_PORT"
}

write_env_config() {
    local env_file="$1"

    cat > "$env_file" << EOF
# Polymarket API Configuration
POLYMARKET_API_URL=https://gamma-api.polymarket.com
POLYMARKET_WS_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
POLYMARKET_DB_PATH=polymarket.db

# HTTP Settings
POLYMARKET_HTTP_TIMEOUT=10
POLYMARKET_HTTP_RPS=2

# WebSocket Settings
POLYMARKET_WS_HEARTBEAT_SEC=15
POLYMARKET_WS_RECONNECT_SEC=5

# Web Dashboard Settings
POLYMARKET_WEB_ENABLED=true
POLYMARKET_WEB_HOST=0.0.0.0
POLYMARKET_WEB_PORT=${WEB_PORT}

# Forward Server Settings
POLYMARKET_FORWARD_ENABLED=true
POLYMARKET_FORWARD_HOST=0.0.0.0
POLYMARKET_FORWARD_PORT=${FORWARD_PORT}

# Scanner Settings
POLYMARKET_SCAN_INTERVAL_SEC=300
POLYMARKET_CATEGORY=

# Logging
POLYMARKET_LOG_LEVEL=INFO
EOF
}

# ==================== Main Installation ====================
TOTAL_STEPS=7

main() {
    print_banner

    # Detect system
    detect_system

    echo -e "  ${DIM}System: $OS_TYPE${OS_ID:+ ($OS_ID)}${NC}"
    echo -e "  ${DIM}Package Manager: ${PKG_MANAGER:-none detected}${NC}"

    # Step 1: Check and install prerequisites
    print_step 1 "Checking system dependencies..."

    # Check/Install curl
    if check_command curl; then
        print_info "curl found"
    else
        if [ -n "$PKG_MANAGER" ]; then
            install_curl && print_info "curl installed" || {
                print_error "Failed to install curl"
                exit 1
            }
        else
            print_error "curl is required. Please install manually."
            exit 1
        fi
    fi

    # Check/Install git
    if check_command git; then
        print_info "Git found"
    else
        if [ -n "$PKG_MANAGER" ]; then
            install_git && print_info "Git installed" || {
                print_error "Failed to install Git"
                exit 1
            }
        else
            print_error "Git is required. Please install manually."
            exit 1
        fi
    fi

    # Step 2: Check/Install Python
    print_step 2 "Checking Python environment..."

    if check_command python3; then
        PYTHON_VER=$(python3 --version 2>&1 | cut -d' ' -f2)
        print_info "Python $PYTHON_VER found"
    else
        if [ -n "$PKG_MANAGER" ]; then
            install_python
            if check_command python3; then
                PYTHON_VER=$(python3 --version 2>&1 | cut -d' ' -f2)
                print_info "Python $PYTHON_VER installed"
            else
                print_error "Failed to install Python"
                exit 1
            fi
        else
            print_error "Python 3 is required but not found"
            echo -e "\n  Please install Python 3.10+ manually"
            exit 1
        fi
    fi

    # Step 3: Install uv
    print_step 3 "Setting up uv package manager..."

    if check_command uv; then
        UV_VER=$(uv --version 2>&1 | head -1 | cut -d' ' -f2)
        print_info "uv $UV_VER already installed"
    else
        echo -e "  ${ARROW} Installing uv..."
        curl -LsSf https://astral.sh/uv/install.sh | sh

        # Add to PATH for current session
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

        if check_command uv; then
            UV_VER=$(uv --version 2>&1 | head -1 | cut -d' ' -f2)
            print_info "uv $UV_VER installed"
        else
            print_error "Failed to install uv"
            exit 1
        fi
    fi

    # Step 4: Configure ports
    print_step 4 "Configuring ports..."
    configure_ports

    # Step 5: Clone repository
    print_step 5 "Setting up repository..."

    if [ -d "$INSTALL_DIR" ]; then
        print_info "Found existing installation: $INSTALL_DIR"
        echo ""
        echo -e "  ${BOLD}Options:${NC}"
        echo -e "    ${CYAN}1${NC}) Update - Pull latest changes (recommended)"
        echo -e "    ${CYAN}2${NC}) Reinstall - Remove and clone fresh"
        echo -e "    ${CYAN}3${NC}) Keep - Use existing without changes"
        echo ""
        read -p "  Select option [1]: " -n 1 -r install_choice
        echo

        case "${install_choice:-1}" in
            2)
                echo -e "  ${ARROW} Removing existing installation..."
                rm -rf "$INSTALL_DIR"
                echo -e "  ${ARROW} Cloning fresh copy..."
                git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
                print_info "Repository reinstalled"
                ;;
            3)
                print_info "Using existing installation without changes"
                ;;
            *)
                cd "$INSTALL_DIR"
                echo -e "  ${ARROW} Pulling latest changes..."
                git fetch origin main 2>/dev/null
                git reset --hard origin/main 2>/dev/null || git pull origin main 2>/dev/null || true
                print_info "Updated to latest version"
                ;;
        esac
    else
        echo -e "  ${ARROW} Cloning repository..."
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
        print_info "Repository cloned to $INSTALL_DIR"
    fi

    cd "$INSTALL_DIR"

    # Step 6: Setup virtual environment
    print_step 6 "Installing dependencies..."

    echo -e "  ${ARROW} Creating virtual environment..."
    uv venv .venv

    echo -e "  ${ARROW} Installing Python packages..."
    source .venv/bin/activate
    uv pip install -e . 2>&1 | while read line; do
        echo -ne "\r  ${DIM}Installing packages...${NC}    "
    done
    echo -ne "\r"
    print_info "Dependencies installed"

    # Step 7: Finalize
    print_step 7 "Finalizing setup..."

    # Create .env with configured ports
    write_env_config ".env"
    print_info "Created .env with custom ports (Web: $WEB_PORT, Forward: $FORWARD_PORT)"

    chmod +x start.sh
    print_info "Made start.sh executable"

    # Get local IP for display
    LOCAL_IP=""
    if [ "$OS_TYPE" = "linux" ]; then
        LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    elif [ "$OS_TYPE" = "macos" ]; then
        LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
    fi

    # ==================== Done ====================
    echo -e "\n${GREEN}${BOLD}━━━ Installation Complete! ━━━${NC}\n"

    echo -e "  ${BOLD}Location:${NC} $INSTALL_DIR"
    echo ""
    echo -e "  ${BOLD}Quick Start:${NC}"
    echo -e "    cd $INSTALL_DIR"
    echo -e "    ./start.sh"
    echo ""
    echo -e "  ${BOLD}Access URLs:${NC}"
    echo -e "    ${ARROW} Local:     ${CYAN}http://localhost:${WEB_PORT}${NC}"
    if [ -n "$LOCAL_IP" ]; then
        echo -e "    ${ARROW} Network:   ${CYAN}http://$LOCAL_IP:${WEB_PORT}${NC}"
    fi
    echo -e "    ${ARROW} WebSocket: ${CYAN}ws://0.0.0.0:${FORWARD_PORT}${NC}"
    echo ""

    # Ask to start now
    read -p "  Start the application now? [Y/n] " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Nn]$ ]]; then
        echo ""
        exec ./start.sh
    else
        echo -e "\n  ${DIM}Run ./start.sh when ready${NC}\n"
    fi
}

# Run main function
main "$@"
