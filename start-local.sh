#!/bin/bash
# Responsibility: Provide macOS/POSIX local start, status, and stop entry points compatible with macOS Bash 3.2.
# Implementation: Locate the repository from the script, create the platform virtual environment, check and install the original dependency manifest, then call the shared Python supervisor.
# Relationships: backend/tools/local_server.py manages services; start-local.ps1 remains the Windows entry point; the shared supervisor reads the root .env.
# Directory:
# - usage: Display command-line help.
# - fail: Report an error and exit.
# Variable index:
# - PROJECT_ROOT: Absolute repository path.
# - ACTION: Selects start, status, or stop.
# - PYTHON: Interpreter used to create the initial environment.
# - BREW_SERVICE: Explicit Homebrew formula name.
# - NO_BROWSER: Controls browser opening.
# - VENV_PYTHON/LAUNCHER: Local runtime entry paths.
# - HASH_PATH/DEPENDENCY_HASH/SAVED_HASH: Track the original four dependency-manifest hashes.
# - INSTALL_LOG: Captures pip installation output.
# - LAUNCHER_ARGS: Safe argv array.
# Constraints: Requires Python 3.11+ and a configured database. It does not source .env, install system software, overwrite another platform's .venv, or retry installation.
set -euo pipefail

# Function: Display entry-point usage.
# Inputs: No parameters.
# Outputs: Help text on standard output.
# Logic: List shared actions and the macOS-specific database-start option.
# Constraints: Does not access files, databases, or external services.
usage() {
    cat <<'USAGE'
Usage: bash start-local.sh [start|status|stop] [--python /path/to/python3] [--no-browser]
                          [--brew-service postgresql@16]
Default action: start. Requires Python 3.11+ and a configured local database.
--brew-service: macOS only; run an already installed PostgreSQL Homebrew service.
No database, credentials, or analysis settings are replaced automatically.
USAGE
}

# Function: Report an argument, dependency, or environment error clearly.
# Inputs: The first positional parameter is redacted error text.
# Outputs: A stderr message and exit status 1.
# Logic: Preserve failure semantics and point to an actionable configuration or log location.
# Constraints: Callers must not provide secrets or full database connection strings.
fail() {
    printf '[setup] ERROR: %s\n' "$1" >&2
    exit 1
}

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ACTION=start
PYTHON=python3
BREW_SERVICE=
NO_BROWSER=false
if [[ $# -gt 0 ]]; then
    case "$1" in
        start|status|stop) ACTION="$1"; shift ;;
    esac
fi
while [[ $# -gt 0 ]]; do
    case "$1" in
        --python|--brew-service)
            [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "$1 requires a value."
            if [[ "$1" == --python ]]; then PYTHON="$2"; else BREW_SERVICE="$2"; fi
            shift 2 ;;
        --no-browser) NO_BROWSER=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "Unknown argument: $1. Use --help." ;;
    esac
done
if [[ -n "$BREW_SERVICE" ]]; then
    [[ "$(uname -s)" == Darwin ]] || fail '--brew-service is available only on macOS.'
    [[ "$BREW_SERVICE" =~ ^postgresql(@[0-9]+)?$ ]] || fail '--brew-service must name an installed PostgreSQL formula.'
fi

VENV_PYTHON="$PROJECT_ROOT/.venv/bin/python"
LAUNCHER="$PROJECT_ROOT/backend/tools/local_server.py"
if [[ ! -x "$VENV_PYTHON" ]]; then
    [[ "$ACTION" == start ]] || fail 'No local environment exists. Run start first.'
    [[ ! -e "$PROJECT_ROOT/.venv" ]] || fail '.venv exists without a usable POSIX Python. Move it aside explicitly before creating a new environment.'
    "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || fail 'Python 3.11+ is required. Use --python with its executable path.'
    "$PYTHON" -m venv "$PROJECT_ROOT/.venv" || fail 'Virtual environment creation failed.'
fi

if [[ "$ACTION" == start ]]; then
    "$VENV_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || fail 'The existing virtual environment needs Python 3.11+.'
    HASH_PATH="$PROJECT_ROOT/.venv/salesmate-unix-requirements.sha256"
    DEPENDENCY_HASH="$("$VENV_PYTHON" -c 'import hashlib,sys; from pathlib import Path; print(hashlib.sha256(b"\0".join(Path(p).read_bytes() for p in sys.argv[1:])).hexdigest())' \
        "$PROJECT_ROOT/requirements.txt" "$PROJECT_ROOT/backend/requirements/dev.txt" \
        "$PROJECT_ROOT/backend/requirements/base.txt" "$PROJECT_ROOT/agent/requirements.txt")"
    SAVED_HASH=
    if [[ -f "$HASH_PATH" ]]; then SAVED_HASH="$(cat "$HASH_PATH")"; fi
    if [[ "$SAVED_HASH" != "$DEPENDENCY_HASH" ]]; then
        INSTALL_LOG="$PROJECT_ROOT/.venv/salesmate-install.log"
        printf '[setup] Installing project dependencies. Log: %s\n' "$INSTALL_LOG"
        if ! "$VENV_PYTHON" -X utf8 -m pip install --disable-pip-version-check --retries 0 \
            -r "$PROJECT_ROOT/requirements.txt" >"$INSTALL_LOG" 2>&1; then
            fail "Dependency installation failed. Inspect $INSTALL_LOG"
        fi
        printf '%s\n' "$DEPENDENCY_HASH" >"$HASH_PATH"
    fi
    "$VENV_PYTHON" -m pip check || fail 'Dependency consistency check failed.'
fi

LAUNCHER_ARGS=("$LAUNCHER" "$ACTION")
if [[ -n "$BREW_SERVICE" ]]; then LAUNCHER_ARGS+=(--brew-service "$BREW_SERVICE"); fi
if [[ "$NO_BROWSER" == true ]]; then LAUNCHER_ARGS+=(--no-browser); fi
exec "$VENV_PYTHON" -X utf8 "${LAUNCHER_ARGS[@]}"
