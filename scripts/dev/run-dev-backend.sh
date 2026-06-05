#!/usr/bin/env bash
# scripts/dev/run-dev-backend.sh — flip NPM to this laptop and run ONLY the backend
# in the FOREGROUND with live logs, so you can watch a Health Auto Export POST land
# at /ingest/hae through the real CF→NPM→laptop path. Reverts NPM on exit.
#
# This is the focused launcher for the HAE end-to-end test. For the full screen-based
# stack (backend + mcp), use start-dev-stack.sh instead.
#
# Usage:
#   ./scripts/dev/run-dev-backend.sh                 flip + run backend (foreground)
#   ./scripts/dev/run-dev-backend.sh --no-flip       localhost:8000 only, no NPM change
#   ./scripts/dev/run-dev-backend.sh --ip <addr>     force the laptop IP NPM points to
#   ./scripts/dev/run-dev-backend.sh --max-runtime "30 minutes"   watchdog deadline
#
# Reads .env.dev at the repo root (copy from .env.dev.example). Ctrl-C tears down:
# reverts NPM and stops uvicorn.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

FLIP=1
IP_OVERRIDE=""
MAX_RUNTIME="2 hours"

usage() { grep '^# ' "$0" | sed 's/^# //'; }
log() { echo "[run-dev-backend] $*"; }
die() { echo "[run-dev-backend] $*" >&2; exit 2; }

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-flip)     FLIP=0 ;;
        --ip)          IP_OVERRIDE="${2:?--ip needs an address}"; shift ;;
        --max-runtime) MAX_RUNTIME="${2:?--max-runtime needs a duration}"; shift ;;
        -h|--help)     usage; exit 0 ;;
        *) die "unknown arg: $1" ;;
    esac
    shift
done

[[ -f .env.dev ]] || die "missing .env.dev — copy .env.dev.example and fill it in"
set -a
# shellcheck disable=SC1091
source .env.dev
set +a

# ─── detect laptop IP (same logic as start-dev-stack.sh) ────────────────────
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
    echo "[run-dev-backend] no interface IP matches LAPTOP_SUBNETS ($LAPTOP_SUBNETS)" >&2
    echo "       available: $addrs" >&2
    exit 2
}

# ─── NPM flip + auto-revert ─────────────────────────────────────────────────
cleaned=0
cleanup() {
    (( cleaned )) && return
    cleaned=1
    if (( FLIP )); then
        log "reverting NPM upstreams → prod"
        ./scripts/dev/npm-flip.sh prod || log "WARN: NPM revert failed — run npm-flip.sh prod manually"
    fi
}
trap cleanup EXIT

if (( FLIP )); then
    LAPTOP_IP="$(detect_ip)"
    log "laptop IP: $LAPTOP_IP"
    log "flipping NPM upstreams → $LAPTOP_IP"
    ./scripts/dev/npm-flip.sh laptop "$LAPTOP_IP"
    ./scripts/dev/npm-flip.sh schedule-watchdog "$MAX_RUNTIME" || \
        log "WARN: could not schedule watchdog; manual revert may be needed"
    PUBLIC="https://healthbridge.example.com"
else
    log "--no-flip: NPM untouched; backend reachable at http://localhost:8000 only"
    PUBLIC="http://localhost:8000"
fi

# ─── backend in dedicated venv, foreground ──────────────────────────────────
cd "$REPO_ROOT/backend"
log "preparing .venv-dev …"
python3 -m venv .venv-dev
# shellcheck disable=SC1091
source .venv-dev/bin/activate
pip install -q -e '.[dev]'

APP_ENV="${APP_ENV:-dev} hosted from $(hostname -s)"
export APP_ENV
log "backend up. Send a Health Auto Export → ${PUBLIC}/ingest/hae"
log "  quick replay of a captured file:  ./scripts/dev/send-hae.sh <export.json>"
log "  Ctrl-C to stop (NPM auto-reverts)."

# Foreground (NOT exec) so the EXIT trap runs the NPM revert on Ctrl-C.
# --no-server-header: don't advertise uvicorn at the origin (CF masks it anyway).
uvicorn healthbridge.app:app --host 0.0.0.0 --port 8000 --reload --no-server-header
