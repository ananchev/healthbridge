#!/usr/bin/env bash
# scripts/dev/start-dev-stack.sh — main local-dev launcher for HealthBridge.
#
# Mirrors the cycling-coach pattern:
#   - flips NPM upstreams (healthbridge + mcp-sleep proxies) to this laptop's LAN IP
#     so the real public path (Cloudflare → NPM → laptop) hits local code
#   - spawns a `healthbridge-dev` screen session with backend + mcp windows
#   - runs each service in a DEDICATED venv (.venv-dev) via uvicorn/python — no Docker
#   - injects APP_ENV banner so a flipped local session is visually distinct
#   - schedules a remote watchdog as a fallback and auto-reverts NPM on exit
#
# Usage: ./scripts/dev/start-dev-stack.sh [--no-flip] [--no-mcp]
#                                         [--ip <addr>] [--max-runtime <duration>]
#
# Reads .env.dev at the repo root (copy from .env.dev.example).
#
# NOTE for Claude Code: this is a working-shaped script but treat the NPM API
# specifics as TODO to verify against the real NPM instance. Keep the rollback
# wholesale-PUT approach from cycling-coach/npm-flip.sh (store full writable proxy
# object, restore by PUTting it back — handles per-location overrides).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

FLIP=1
RUN_MCP=1
IP_OVERRIDE=""
MAX_RUNTIME="2 hours"

usage() {
    grep '^# ' "$0" | sed 's/^# //'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-flip)     FLIP=0 ;;
        --no-mcp)      RUN_MCP=0 ;;
        --ip)          IP_OVERRIDE="${2:?}"; shift ;;
        --max-runtime) MAX_RUNTIME="${2:?}"; shift ;;
        -h|--help)     usage; exit 0 ;;
        *) echo "start-dev-stack.sh: unknown arg: $1" >&2; usage; exit 2 ;;
    esac
    shift
done

if [[ ! -f .env.dev ]]; then
    echo "start-dev-stack.sh: missing .env.dev — copy .env.dev.example and fill it in" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source .env.dev
set +a

log() { echo "[start-dev-stack.sh] $*"; }

# ─── detect laptop IP ────────────────────────────────────────────────────
detect_ip() {
    if [[ -n "$IP_OVERRIDE" ]]; then echo "$IP_OVERRIDE"; return; fi
    local addrs
    addrs="$(ip -4 -o addr show 2>/dev/null | awk '{print $4}' | cut -d/ -f1 || \
             ifconfig 2>/dev/null | awk '/inet /{print $2}')"
    for prefix in $LAPTOP_SUBNETS; do
        for a in $addrs; do
            [[ "$a" == "$prefix"* ]] && { echo "$a"; return; }
        done
    done
    echo "start-dev-stack.sh: no interface IP matches LAPTOP_SUBNETS ($LAPTOP_SUBNETS)" >&2
    echo "       available: $(echo "$addrs")" >&2
    exit 2
}

LAPTOP_IP="$(detect_ip)"
log "laptop IP: $LAPTOP_IP"

# ─── NPM flip ──────────────────────────────────────────────────────────────
if (( FLIP )); then
    log "flipping NPM upstreams → $LAPTOP_IP"
    ./scripts/dev/npm-flip.sh laptop "$LAPTOP_IP"
    # Schedule the remote watchdog as a safety net (auto-revert if we crash).
    ./scripts/dev/npm-flip.sh schedule-watchdog "$MAX_RUNTIME" || \
        log "WARN: could not schedule watchdog; manual revert may be needed"
fi

cleanup() {
    log "cleanup: reaping dev processes"
    pkill -f 'healthbridge-dev-backend|healthbridge-dev-mcp' 2>/dev/null || true
    if (( FLIP )); then
        log "reverting NPM upstreams → prod"
        ./scripts/dev/npm-flip.sh prod || log "WARN: NPM revert failed — run npm-flip.sh prod manually"
    fi
}
trap cleanup INT TERM HUP EXIT

# ─── banner ──────────────────────────────────────────────────────────────
# Hostname (not IP) to avoid privacy-extension WebRTC-leak stripping.
BANNER_ENV="${APP_ENV:-dev} hosted from $(hostname -s)"

# ─── spawn screen session ────────────────────────────────────────────────
screen -S healthbridge-dev -X quit 2>/dev/null || true

# Backend window — fresh venv install + uvicorn against dev.duckdb.
screen -dmS healthbridge-dev -t backend bash -c "
    cd '$REPO_ROOT/backend'
    set -a; source '$REPO_ROOT/.env.dev'; set +a
    export APP_ENV='$BANNER_ENV'
    # NOTE: do NOT set HEALTHBRIDGE_DEV here — dev e2e goes through CF→NPM and MUST
    # exercise the real bearer-token auth. HEALTHBRIDGE_DEV=1 is only for hitting
    # localhost directly (the --no-flip path), set it yourself if you do that.
    echo '[backend] creating/using dedicated venv .venv-dev …'
    python3 -m venv .venv-dev
    source .venv-dev/bin/activate
    pip install -q -e '.[dev]' >/tmp/healthbridge-dev-backend.log 2>&1 || { echo 'INSTALL FAILED — see /tmp/healthbridge-dev-backend.log'; exec bash; }
    echo '[backend] uvicorn on 0.0.0.0:8000 (APP_ENV=$BANNER_ENV) — reached via CF→NPM→laptop'
    exec uvicorn healthbridge.app:app --host 0.0.0.0 --port 8000 --reload
"

if (( RUN_MCP )); then
    screen -S healthbridge-dev -X screen -t mcp bash -c "
        cd '$REPO_ROOT/mcp'
        set -a; source '$REPO_ROOT/.env.dev'; set +a
        export APP_ENV='$BANNER_ENV'
        echo '[mcp] creating/using dedicated venv .venv-dev …'
        python3 -m venv .venv-dev
        source .venv-dev/bin/activate
        pip install -q -e '.[dev]' >/tmp/healthbridge-dev-mcp.log 2>&1 || { echo 'INSTALL FAILED — see /tmp/healthbridge-dev-mcp.log'; exec bash; }
        echo '[mcp] starting sleep-mcp on 0.0.0.0:8001'
        exec python -m sleep_mcp.server
    "
fi

log "screen session 'healthbridge-dev' up. Attach: screen -r healthbridge-dev"
log "  Ctrl-A 0/1 to switch backend/mcp windows; Ctrl-A d to detach (NPM auto-reverts on exit)."
log "Tailing — Ctrl-C to tear down (reverts NPM, reaps processes)."

# Keep the launcher in the foreground so the trap runs on Ctrl-C.
while screen -list | grep -q healthbridge-dev; do sleep 5; done
