#!/bin/bash
# Tavily CLI installer
# Usage: curl -fsSL https://tavily.com/install.sh | bash

set -e

BOLD="\033[1m"
GREEN="\033[0;32m"
RED="\033[0;31m"
RESET="\033[0m"

echo ""
echo -e "${BOLD}Tavily CLI Installer${RESET}"
echo ""

# Check for Python 3.10+
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: python3 is required but not found.${RESET}"
    echo "Install Python 3.10+ from https://python.org"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
    echo -e "${RED}Error: Python 3.10+ is required (found $PYTHON_VERSION).${RESET}"
    exit 1
fi

echo "Found Python $PYTHON_VERSION"

# Check for pip
if ! python3 -m pip --version &> /dev/null; then
    echo -e "${RED}Error: pip is required but not found.${RESET}"
    echo "Install pip: python3 -m ensurepip --upgrade"
    exit 1
fi

# Install tavily-cli
echo "Installing tavily-cli..."
python3 -m pip install --quiet --upgrade tavily-cli 2>&1 || {
    echo ""
    echo "pip install failed. Trying with --user flag..."
    python3 -m pip install --quiet --upgrade --user tavily-cli 2>&1
}

# Verify installation
if command -v tavily &> /dev/null; then
    VERSION=$(tavily --version 2>/dev/null || echo "unknown")
    echo ""
    echo -e "${GREEN}Tavily CLI installed successfully!${RESET}"
    echo "  Version: $VERSION"
    echo ""
    echo "Get started:"
    echo "  export TAVILY_API_KEY=\"your_api_key\"   # get one at https://tavily.com"
    echo "  tavily search \"your first query\" --json"
    echo ""
else
    echo ""
    echo -e "${GREEN}tavily-cli installed.${RESET}"
    echo ""
    echo "If 'tavily' is not in your PATH, add pip's bin directory:"
    echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi
