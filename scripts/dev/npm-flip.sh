#!/usr/bin/env bash
# scripts/dev/npm-flip.sh — flip Nginx-Proxy-Manager upstreams between prod and laptop.
#
# Adapted from the cycling-coach npm-flip.sh. Key correctness property carried over:
# rollback stores the FULL writable proxy object (not just forward_host), and flip
# mutates BOTH the top-level forward_host AND every location's forward_host. NPM
# matches per-location rules before the default, so flipping only the default would
# leak any per-path override (e.g. /stats) straight back to prod. Restore PUTs the
# saved object back wholesale.
#
# Commands:
#   npm-flip.sh laptop <ip>          flip both proxies (and their locations) to <ip>
#   npm-flip.sh prod                 restore from rollback file
#   npm-flip.sh status               show current upstreams incl. per-location
#   npm-flip.sh schedule-watchdog <duration>   install host-side auto-revert (see watchdog.sh)
#
# Reads .env.dev for NPM connection details (see .env.dev.example).
#
# NOTE for Claude Code: implement the API calls (get_token/read_proxy/put_proxy/
# writable_only) as in cycling-coach. Verify the NPM API schema against the running
# instance. Keep the legacy-string rollback backward-compat branch from the source.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"
set -a; source .env.dev; set +a

: "${NPM_BASE_URL:?set NPM_BASE_URL in .env.dev}"
: "${NPM_API_USER:?}"; : "${NPM_API_PASSWORD:?}"
: "${NPM_HEALTHBRIDGE_PROXY_ID:?}"; : "${NPM_MCP_PROXY_ID:?}"
ROLLBACK_FILE="${NPM_ROLLBACK_PATH:-/tmp/healthbridge-npm-rollback.json}"
DRY_RUN="${DRY_RUN:-0}"

die() { echo "npm-flip: $*" >&2; exit 1; }

# --- NPM API helpers (TODO: implement per cycling-coach) -------------------
get_token()      { :; }   # POST /api/tokens → bearer
read_proxy()     { :; }   # GET /api/nginx/proxy-hosts/<id>
put_proxy()      { :; }   # PUT /api/nginx/proxy-hosts/<id>
writable_only()  { :; }   # strip read-only fields NPM rejects on PUT

# Build the laptop-flipped version: forward_host + every location → laptop ip.
flip_to_laptop_ip() {
    local writable="$1" ip="$2"
    echo "$writable" | jq --arg ip "$ip" '
        .forward_host = $ip
        | .locations = ((.locations // []) | map(.forward_host = $ip))
    '
}

cmd_status() {
    : # TODO: print top-level + per-location upstreams for both proxy IDs.
}

cmd_laptop() {
    local ip="${1:-}"; [[ -n "$ip" ]] || die "usage: npm-flip.sh laptop <ip>"
    # TODO:
    #  token=get_token
    #  read both proxies; save FULL writable objects to ROLLBACK_FILE keyed by id
    #  PUT flip_to_laptop_ip(writable, ip) for each
    :
}

cmd_prod() {
    [[ -f "$ROLLBACK_FILE" ]] || die "no rollback at $ROLLBACK_FILE"
    # TODO: for each proxy id, read saved object; if it's a legacy string, restore
    # forward_host only; else PUT the saved writable object back wholesale.
    :
}

cmd_schedule_watchdog() {
    local duration="${1:?usage: npm-flip.sh schedule-watchdog <duration>}"
    # TODO: install/refresh watchdog.sh on the NPM host (or via docker exec if
    # NPM runs in a container) to auto-run `npm-flip.sh prod` after <duration>.
    echo "npm-flip: watchdog scheduled for $duration (stub)"
}

case "${1:-}" in
    laptop)            shift; cmd_laptop "$@" ;;
    prod)              cmd_prod ;;
    status)            cmd_status ;;
    schedule-watchdog) shift; cmd_schedule_watchdog "$@" ;;
    *) die "usage: laptop <ip> | prod | status | schedule-watchdog <duration>" ;;
esac
