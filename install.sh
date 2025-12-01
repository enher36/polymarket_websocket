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
    if command -v "$1" &> /dev/null; then
        return 0
    else
        return 1
    fi
}

# ==================== Main Installation ====================
TOTAL_STEPS=5

main() {
    print_banner

    # Step 1: Check prerequisites
    print_step 1 "Checking prerequisites..."

    # Check Python
    if check_command python3; then
        PYTHON_VER=$(python3 --version 2>&1 | cut -d' ' -f2)
        print_info "Python $PYTHON_VER found"
    else
        print_error "Python 3 is required but not found"
        echo -e "\n  Please install Python 3.10+ first:"
        echo "    Ubuntu/Debian: sudo apt install python3 python3-pip"
        echo "    macOS: brew install python3"
        echo "    Windows: https://www.python.org/downloads/"
        exit 1
    fi

    # Check git
    if check_command git; then
        print_info "Git found"
    else
        print_error "Git is required but not found"
        echo -e "\n  Please install Git first:"
        echo "    Ubuntu/Debian: sudo apt install git"
        echo "    macOS: brew install git"
        exit 1
    fi

    # Check curl
    if check_command curl; then
        print_info "curl found"
    else
        print_error "curl is required but not found"
        exit 1
    fi

    # Step 2: Install uv if needed
    print_step 2 "Setting up uv package manager..."

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
            print_info "uv $UV_VER installed successfully"
        else
            print_error "Failed to install uv"
            echo -e "\n  Please install manually: curl -LsSf https://astral.sh/uv/install.sh | sh"
            exit 1
        fi
    fi

    # Step 3: Clone repository
    print_step 3 "Cloning repository..."

    if [ -d "$INSTALL_DIR" ]; then
        print_warn "Directory already exists: $INSTALL_DIR"
        read -p "  Remove and reinstall? [y/N] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            rm -rf "$INSTALL_DIR"
            echo -e "  ${ARROW} Cloning from GitHub..."
            git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
            print_info "Repository cloned"
        else
            print_info "Using existing installation"
        fi
    else
        echo -e "  ${ARROW} Cloning from GitHub..."
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
        print_info "Repository cloned to $INSTALL_DIR"
    fi

    cd "$INSTALL_DIR"

    # Step 4: Setup virtual environment and install dependencies
    print_step 4 "Installing dependencies..."

    echo -e "  ${ARROW} Creating virtual environment..."
    uv venv .venv

    echo -e "  ${ARROW} Installing Python packages..."
    source .venv/bin/activate
    uv pip install -e . 2>&1 | while read line; do
        echo -ne "\r  ${DIM}Installing packages...${NC}    "
    done
    echo -ne "\r"
    print_info "Dependencies installed"

    # Step 5: Create .env if needed
    print_step 5 "Finalizing setup..."

    if [ -f ".env.example" ] && [ ! -f ".env" ]; then
        cp .env.example .env
        print_info "Created .env configuration file"
    fi

    chmod +x start.sh
    print_info "Made start.sh executable"

    # ==================== Done ====================
    echo -e "\n${GREEN}${BOLD}━━━ Installation Complete! ━━━${NC}\n"

    echo -e "  ${BOLD}Location:${NC} $INSTALL_DIR"
    echo ""
    echo -e "  ${BOLD}Quick Start:${NC}"
    echo -e "    cd $INSTALL_DIR"
    echo -e "    ./start.sh"
    echo ""
    echo -e "  ${BOLD}Or run directly:${NC}"
    echo -e "    $INSTALL_DIR/start.sh"
    echo ""
    echo -e "  ${BOLD}Features:${NC}"
    echo -e "    ${ARROW} Web Dashboard:   ${CYAN}http://127.0.0.1:8080${NC}"
    echo -e "    ${ARROW} Forward Server:  ${CYAN}ws://0.0.0.0:8765${NC}"
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
