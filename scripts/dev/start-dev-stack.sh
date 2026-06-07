#!/usr/bin/env bash
# scripts/dev/start-dev-stack.sh — the single local-dev launcher for HealthBridge.
#
# Flips NPM upstreams (healthbridge + mcp-healthbridge proxies) to this laptop's LAN
# IP so the real public path (Cloudflare → NPM → laptop) hits local code, then runs
# the backend and sleep-mcp IN THE FOREGROUND with combined, prefixed logs. Ctrl-C
# stops both services and reverts NPM. No screen, no Docker — dedicated .venv-dev per
# service via uvicorn/python.
#
# Usage:
#   ./scripts/dev/start-dev-stack.sh                 flip + run backend + sleep-mcp
#   ./scripts/dev/start-dev-stack.sh --backend-only  backend only (e.g. HAE ingest test)
#   ./scripts/dev/start-dev-stack.sh --mcp-only      sleep-mcp only (e.g. claude.ai MCP test)
#   ./scripts/dev/start-dev-stack.sh --no-flip       run locally, no NPM change
#   ./scripts/dev/start-dev-stack.sh --ip <addr>     force the laptop IP NPM points to
#   ./scripts/dev/start-dev-stack.sh --max-runtime "30 minutes"   watchdog deadline
#
# Reads .env.dev at the repo root (copy from .env.dev.example). sleep-mcp auth is ON
# whenever MCP_OAUTH_SIGNING_KEY is set in .env.dev; export HEALTHBRIDGE_MCP_DEV=1 for
# the --no-flip localhost path to bypass it.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

FLIP=1
RUN_BACKEND=1
RUN_MCP=1
IP_OVERRIDE=""
MAX_RUNTIME="2 hours"

usage() { grep '^# ' "$0" | sed 's/^# //'; }
log() { echo "[dev-stack] $*"; }
die() { echo "[dev-stack] $*" >&2; exit 2; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-flip)      FLIP=0 ;;
        --backend-only) RUN_MCP=0 ;;
        --mcp-only)     RUN_BACKEND=0 ;;
        --ip)           IP_OVERRIDE="${2:?--ip needs an address}"; shift ;;
        --max-runtime)  MAX_RUNTIME="${2:?--max-runtime needs a duration}"; shift ;;
        -h|--help)      usage; exit 0 ;;
        *) die "unknown arg: $1" ;;
    esac
    shift
done

[[ -f .env.dev ]] || die "missing .env.dev — copy .env.dev.example and fill it in"
set -a
# shellcheck disable=SC1091
source .env.dev
set +a

# ─── detect laptop IP ────────────────────────────────────────────────────────
detect_ip() {
    if [[ -n "$IP_OVERRIDE" ]]; then echo "$IP_OVERRIDE"; return; fi
    local addrs
    addrs="$(ip -4 -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || \
             ifconfig 2>/dev/null | awk '/inet /{print $2}')"
    for prefix in ${LAPTOP_SUBNETS:-}; do
        for a in $addrs; do
            [[ "$a" == "$prefix"* ]] && { echo "$a"; return; }
        done
    done
    die "no interface IP matches LAPTOP_SUBNETS (${LAPTOP_SUBNETS:-unset}); available: $addrs"
}

# ─── flip + auto-revert ──────────────────────────────────────────────────────
cleaned=0
cleanup() {
    (( cleaned )) && return
    cleaned=1
    log "stopping services …"
    pkill -P $$ 2>/dev/null || true
    if (( FLIP )); then
        log "reverting NPM upstreams → prod"
        ./scripts/dev/npm-flip.sh prod || log "WARN: NPM revert failed — run npm-flip.sh prod manually"
    fi
}
trap cleanup EXIT INT TERM

if (( FLIP )); then
    LAPTOP_IP="$(detect_ip)"
    log "laptop IP: $LAPTOP_IP"
    log "flipping NPM upstreams → $LAPTOP_IP"
    ./scripts/dev/npm-flip.sh laptop "$LAPTOP_IP"
    ./scripts/dev/npm-flip.sh schedule-watchdog "$MAX_RUNTIME" || \
        log "WARN: could not schedule watchdog; manual revert may be needed"
fi

BANNER_ENV="${APP_ENV:-dev} hosted from $(hostname -s)"

# ─── service runners (backgrounded, prefixed combined logs) ──────────────────
start_backend() {
    (
        cd "$REPO_ROOT/backend"
        python3 -m venv .venv-dev
        # shellcheck disable=SC1091
        source .venv-dev/bin/activate
        pip install -q -e '.[dev]'
        export APP_ENV="$BANNER_ENV"
        # NOTE: do NOT set HEALTHBRIDGE_DEV here — the flipped CF→NPM→laptop path
        # must exercise real bearer auth. Set it yourself only for --no-flip.
        exec uvicorn healthbridge.app:app --host 0.0.0.0 --port 8000 --reload --no-server-header
    ) 2>&1 | awk '{ print "[backend] " $0; fflush() }' &
}

start_mcp() {
    (
        cd "$REPO_ROOT/mcp"
        python3 -m venv .venv-dev
        # shellcheck disable=SC1091
        source .venv-dev/bin/activate
        pip install -q -e '.[dev]'
        # HEALTHBRIDGE_DB in .env.dev is relative to backend/; resolve to the real
        # backend dev DB (opened read-only) since our cwd is mcp/.
        export HEALTHBRIDGE_DB="$REPO_ROOT/backend/dev.duckdb"
        exec python -m sleep_mcp.server
    ) 2>&1 | awk '{ print "[mcp] " $0; fflush() }' &
}

(( RUN_BACKEND )) && { log "starting backend on 0.0.0.0:8000"; start_backend; }
(( RUN_MCP ))     && { log "starting sleep-mcp on 0.0.0.0:8001"; start_mcp; }

log "stack up. Ctrl-C to stop (services reaped, NPM reverted)."
wait
