#!/usr/bin/env bash
# scripts/dev/send-hae.sh — POST a captured Health Auto Export JSON to /ingest/hae
# using the dev bearer token. Lets you exercise the full adapter path with a file you
# already exported, without scheduling the phone. POST twice to prove idempotency.
#
# Usage:
#   ./scripts/dev/send-hae.sh <export.json>             → public CF→NPM→laptop path
#   ./scripts/dev/send-hae.sh <export.json> --local     → http://localhost:8000
#   ./scripts/dev/send-hae.sh <export.json> --url <base>
#
# Pair with start-dev-stack.sh --backend-only (public) or with --no-flip (--local).
# Reads HEALTHBRIDGE_TOKEN from .env.dev.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

die() { echo "[send-hae] $*" >&2; exit 2; }

FILE=""
BASE="https://healthbridge.example.com"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --local) BASE="http://localhost:8000" ;;
        --url)   BASE="${2:?--url needs a base URL}"; shift ;;
        -h|--help) grep '^# ' "$0" | sed 's/^# //'; exit 0 ;;
        *)       FILE="$1" ;;
    esac
    shift
done

[[ -n "$FILE" ]] || die "usage: send-hae.sh <export.json> [--local|--url <base>]"
[[ -f "$FILE" ]] || die "file not found: $FILE"
[[ -f .env.dev ]] || die "missing .env.dev"

# shellcheck disable=SC1091
source .env.dev
[[ -n "${HEALTHBRIDGE_TOKEN:-}" ]] || die "HEALTHBRIDGE_TOKEN not set in .env.dev"

echo "[send-hae] POST $FILE → ${BASE}/ingest/hae"
resp="$(curl -sS -w '\n%{http_code}' -X POST "${BASE}/ingest/hae" \
    -H "Authorization: Bearer ${HEALTHBRIDGE_TOKEN}" \
    -H 'Content-Type: application/json' \
    --data @"$FILE")"

body="$(echo "$resp" | sed '$d')"
code="$(echo "$resp" | tail -n1)"

echo "[send-hae] HTTP $code"
if command -v jq >/dev/null 2>&1; then
    echo "$body" | jq .
else
    echo "$body"
fi
[[ "$code" == 2* ]] || die "non-2xx response"
