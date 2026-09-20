#!/bin/bash
# 职责：提供 macOS/POSIX 本地启动、状态查询和停止入口，兼容 macOS 自带 Bash 3.2。
# 实现：按脚本位置定位仓库，创建本平台虚拟环境，检查依赖摘要并安装原依赖，再调用共享 Python 监督器。
# 关联：backend/tools/local_server.py 管理服务；start-local.ps1 保留 Windows 入口；根 .env 由共享监督器读取。
# 目录：usage 显示参数；fail 报告错误并退出；无类。
# 变量索引：PROJECT_ROOT 为仓库绝对路径；ACTION 选择 start/status/stop；PYTHON 为首次建环境的解释器；
# BREW_SERVICE 为显式 Homebrew 公式名；NO_BROWSER 控制浏览器；VENV_PYTHON、LAUNCHER 为本地入口；
# HASH_PATH、DEPENDENCY_HASH、SAVED_HASH 记录原四份依赖清单摘要；INSTALL_LOG 保存 pip 安装输出；LAUNCHER_ARGS 为安全 argv 数组。
# 约束：要求已安装 Python 3.11+ 及数据库；不 source .env、不安装系统软件、不覆盖其他平台的 .venv、不重试安装。
set -euo pipefail

# 功能：显示入口用法。
# 输入：无参数；输出：帮助文本到标准输出。
# 逻辑：列出共享动作和 macOS 专属数据库启动选项。
# 约束：不访问文件、数据库或外部服务。
usage() {
    cat <<'USAGE'
Usage: bash start-local.sh [start|status|stop] [--python /path/to/python3] [--no-browser]
                          [--brew-service postgresql@16]
Default action: start. Requires Python 3.11+ and a configured local database.
--brew-service: macOS only; run an already installed PostgreSQL Homebrew service.
No database, credentials, or analysis settings are replaced automatically.
USAGE
}

# 功能：明确报告参数、依赖或环境错误。
# 输入：第一个位置参数为已脱敏错误文本；输出：stderr 消息，退出码 1。
# 逻辑：保持失败语义，指向可操作的配置或日志位置。
# 约束：调用者不得传入密钥或完整数据库连接字符串。
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
