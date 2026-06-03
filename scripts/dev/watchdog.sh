#!/usr/bin/env bash
# scripts/dev/watchdog.sh — runs ON THE NPM HOST. Fallback auto-revert.
#
# Scheduled by `npm-flip.sh schedule-watchdog`. If the dev session dies without
# cleanly reverting NPM (laptop crash, network drop), this fires after the deadline
# and restores the prod upstreams from the rollback file.
#
# Usage: watchdog.sh [/path/to/watchdog.conf]
#
# watchdog.conf sets (written by npm-flip.sh schedule-watchdog):
#   NPM_BASE_URL, NPM_API_USER, NPM_API_PASSWORD, NPM_ROLLBACK_PATH
#
# Idempotent: if no rollback file exists, exits cleanly (already restored).

set -euo pipefail

CONF="${1:-/var/lib/healthbridge-dev/watchdog.conf}"

if [[ ! -f "$CONF" ]]; then
    echo "watchdog: no config at $CONF — nothing to do"
    exit 0
fi

# shellcheck disable=SC1090
source "$CONF"

ROLLBACK_FILE="${NPM_ROLLBACK_PATH:-/var/lib/healthbridge-dev/rollback.json}"

if [[ ! -f "$ROLLBACK_FILE" ]]; then
    echo "watchdog: no rollback at $ROLLBACK_FILE — already restored or session was clean"
    exit 0
fi

_get_token() {
    local resp token
    resp="$(curl -s -f -X POST "${NPM_BASE_URL}/api/tokens" \
        -H 'Content-Type: application/json' \
        -d "{\"identity\":\"${NPM_API_USER}\",\"secret\":\"${NPM_API_PASSWORD}\"}")"
    token="$(echo "$resp" | jq -r '.token // empty')"
    if [[ -z "$token" ]]; then
        echo "watchdog: NPM auth failed: $resp" >&2
        exit 1
    fi
    echo "$token"
}

_put_proxy() {
    local id="$1" token="$2" body="$3"
    curl -s -f -X PUT "${NPM_BASE_URL}/api/nginx/proxy-hosts/${id}" \
        -H "Authorization: Bearer ${token}" \
        -H 'Content-Type: application/json' \
        -d "$body" > /dev/null
}

token="$(_get_token)"
rollback="$(cat "$ROLLBACK_FILE")"

while IFS= read -r id; do
    saved="$(echo "$rollback" | jq --arg id "$id" '.[$id]')"
    saved_type="$(echo "$saved" | jq -r 'type')"

    if [[ "$saved_type" == "string" ]]; then
        # Legacy format: just a forward_host value; cannot fully restore without
        # current proxy state. Log and skip rather than partially mutating.
        echo "watchdog: proxy ${id} — legacy rollback format, skipping (manual restore needed)" >&2
        continue
    fi

    _put_proxy "$id" "$token" "$saved"
    echo "watchdog: proxy ${id} restored → $(echo "$saved" | jq -r '.forward_host')"
done < <(echo "$rollback" | jq -r 'keys[]')

rm -f "$ROLLBACK_FILE"
echo "watchdog: done — rollback file removed"
