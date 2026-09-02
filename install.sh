#!/bin/sh
set -e

PACKAGE_NAME="tavily-cli"
COMMAND_NAME="tvly"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=10
FRESH_INSTALL=0
INIT_COMPLETED=0

# Colors (only when outputting to a terminal)
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BLUE='\033[0;34m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' BLUE='' BOLD='' RESET=''
fi

info()  { printf "${BLUE}${BOLD}==>${RESET} %s\n" "$1"; }
warn()  { printf "${YELLOW}${BOLD}warning:${RESET} %s\n" "$1"; }
error() { printf "${RED}${BOLD}error:${RESET} %s\n" "$1" >&2; exit 1; }

# Find a Python >= 3.10 interpreter
find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" >/dev/null 2>&1; then
            version=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null) || continue
            major=$(echo "$version" | cut -d. -f1)
            minor=$(echo "$version" | cut -d. -f2)
            if [ "$major" -gt "$MIN_PYTHON_MAJOR" ] || \
               { [ "$major" -eq "$MIN_PYTHON_MAJOR" ] && [ "$minor" -ge "$MIN_PYTHON_MINOR" ]; }; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

can_run_init_interactively() {
    [ -z "${CI:-}" ] || return 1
    [ -z "${SSH_CONNECTION:-}" ] || return 1
    [ -z "${SSH_TTY:-}" ] || return 1
    [ -t 1 ] || return 1

    # Browser OAuth cannot complete reliably in a display-less Linux session.
    if [ "$(uname -s 2>/dev/null || true)" = "Linux" ] && \
       [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
        return 1
    fi

    # `curl ... | sh` uses stdin for the script, but an interactive terminal
    # remains available through /dev/tty.
    [ -t 0 ] || ( : </dev/tty ) 2>/dev/null
}

run_init_handoff() {
    [ "$FRESH_INSTALL" = "1" ] || return 0

    if ! command -v "$COMMAND_NAME" >/dev/null 2>&1; then
        return 0
    fi

    if ! can_run_init_interactively; then
        return 0
    fi

    if ! "$COMMAND_NAME" init --help >/dev/null 2>&1; then
        warn "The installed CLI does not provide 'tvly init'. Run 'tvly update', then 'tvly init'."
        return 0
    fi

    info "Starting guided Tavily setup..."
    if [ -t 0 ]; then
        if "$COMMAND_NAME" init; then
            INIT_COMPLETED=1
            return 0
        fi
    elif "$COMMAND_NAME" init </dev/tty; then
        INIT_COMPLETED=1
        return 0
    fi

    warn "Guided setup did not complete. The CLI is installed; run 'tvly init' to try again."
}

require_python() {
    if [ -z "${PYTHON:-}" ]; then
        PYTHON=$(find_python) || error "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ is required but not found.
  Install it from https://www.python.org/downloads/ or install uv (https://docs.astral.sh/uv/) and try again."
    fi
}

install_with_pip() {
    require_python

    py_version=$("$PYTHON" --version 2>&1)
    info "Found $py_version"
    info "Installing ${PACKAGE_NAME} with pip..."

    if [ "$PIP_INSTALLED" != "1" ]; then
        FRESH_INSTALL=1
    fi

    # Use --user only when outside a virtual environment
    in_venv=$("$PYTHON" -c "import sys; print(int(sys.prefix != sys.base_prefix or hasattr(sys, 'real_prefix')))" 2>/dev/null) || in_venv=0
    if [ "$in_venv" = "1" ]; then
        "$PYTHON" -m pip install --upgrade "$PACKAGE_NAME"
    else
        "$PYTHON" -m pip install --user --upgrade "$PACKAGE_NAME"

        # Warn if ~/.local/bin is not in PATH (common pip --user location)
        user_bin=$("$PYTHON" -c "import site; print(site.getuserbase() + '/bin')" 2>/dev/null) || true
        if [ -n "$user_bin" ] && ! echo "$PATH" | tr ':' '\n' | grep -qx "$user_bin"; then
            warn "$user_bin is not in your PATH. Add it with:"
            printf "  export PATH=\"%s:\$PATH\"\n\n" "$user_bin"
        fi
    fi
}

main() {
    printf "\n${BOLD}Tavily CLI Installer${RESET}\n\n"

    # Preserve the package manager that already owns Tavily CLI. Only prefer uv
    # for a fresh installation, after checking uv, pipx, and pip ownership.
    UV_AVAILABLE=0
    PIPX_AVAILABLE=0
    UV_INSTALLED=0
    PIPX_INSTALLED=0
    PIP_INSTALLED=0
    PYTHON=""

    if command -v uv >/dev/null 2>&1; then
        UV_AVAILABLE=1
        if uv tool list 2>/dev/null | grep -q "^${PACKAGE_NAME} "; then
            UV_INSTALLED=1
        fi
    fi

    if command -v pipx >/dev/null 2>&1; then
        PIPX_AVAILABLE=1
        if pipx list --short 2>/dev/null | grep -q "^${PACKAGE_NAME} "; then
            PIPX_INSTALLED=1
        fi
    fi

    PYTHON=$(find_python 2>/dev/null || true)
    if [ -n "$PYTHON" ] && "$PYTHON" -m pip show "$PACKAGE_NAME" >/dev/null 2>&1; then
        PIP_INSTALLED=1
    fi

    if [ "$UV_INSTALLED" = "1" ]; then
        info "Installing ${PACKAGE_NAME} with uv..."
        uv tool upgrade "$PACKAGE_NAME"
    elif [ "$PIPX_INSTALLED" = "1" ]; then
        info "Installing ${PACKAGE_NAME} with pipx..."
        pipx upgrade "$PACKAGE_NAME"
    elif [ "$PIP_INSTALLED" = "1" ]; then
        install_with_pip
    elif [ "$UV_AVAILABLE" = "1" ]; then
        # uv is preferred for a fresh install and does not require system Python.
        info "Installing ${PACKAGE_NAME} with uv..."
        FRESH_INSTALL=1
        uv tool install "$PACKAGE_NAME"
    elif [ "$PIPX_AVAILABLE" = "1" ]; then
        info "Installing ${PACKAGE_NAME} with pipx..."
        FRESH_INSTALL=1
        pipx install "$PACKAGE_NAME"
    else
        install_with_pip
    fi

    # Verify
    if command -v "$COMMAND_NAME" >/dev/null 2>&1; then
        installed_version=$("$COMMAND_NAME" --version 2>/dev/null || echo "unknown")
        printf "\n${GREEN}${BOLD}Success!${RESET} ${installed_version} is installed.\n"
    else
        printf "\n${GREEN}${BOLD}Installed!${RESET} You may need to restart your shell or add the install directory to your PATH.\n"
    fi

    run_init_handoff

    printf "\nNext steps:\n"
    if [ "$INIT_COMPLETED" != "1" ]; then
        printf "  ${BOLD}${COMMAND_NAME} init${RESET}        # guided authentication and skill setup\n"
    fi
    printf "  ${BOLD}${COMMAND_NAME} search${RESET} ...  # search the web\n\n"
}

main
