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

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
set -a; source .env.dev; set +a

: "${NPM_BASE_URL:?set NPM_BASE_URL in .env.dev}"
: "${NPM_API_USER:?set NPM_API_USER in .env.dev}"
: "${NPM_API_PASSWORD:?set NPM_API_PASSWORD in .env.dev}"
: "${NPM_HEALTHBRIDGE_PROXY_ID:?set NPM_HEALTHBRIDGE_PROXY_ID in .env.dev}"
: "${NPM_MCP_PROXY_ID:?set NPM_MCP_PROXY_ID in .env.dev}"

# Rollback lives locally on the laptop; NPM_ROLLBACK_PATH is the remote (NPM host) path.
LOCAL_ROLLBACK="/tmp/healthbridge-npm-rollback.json"
DRY_RUN="${DRY_RUN:-0}"

die() { echo "npm-flip: $*" >&2; exit 1; }
log() { echo "npm-flip: $*"; }

# --- NPM API helpers ----------------------------------------------------------

get_token() {
    local resp token
    resp="$(curl -s -f -X POST "${NPM_BASE_URL}/api/tokens" \
        -H 'Content-Type: application/json' \
        -d "{\"identity\":\"${NPM_API_USER}\",\"secret\":\"${NPM_API_PASSWORD}\"}" \
        2>&1)" \
        || die "NPM auth request failed — check NPM_BASE_URL / NPM_API_USER / NPM_API_PASSWORD"
    token="$(echo "$resp" | jq -r '.token // empty')"
    [[ -n "$token" ]] || die "NPM auth: no token in response: $resp"
    echo "$token"
}

read_proxy() {
    local id="$1" token="$2"
    curl -s -f "${NPM_BASE_URL}/api/nginx/proxy-hosts/${id}" \
        -H "Authorization: Bearer ${token}" \
        || die "failed to read proxy host ${id}"
}

put_proxy() {
    local id="$1" token="$2" body="$3"
    curl -s -f -X PUT "${NPM_BASE_URL}/api/nginx/proxy-hosts/${id}" \
        -H "Authorization: Bearer ${token}" \
        -H 'Content-Type: application/json' \
        -d "$body" \
        || die "failed to PUT proxy host ${id}"
}

# Strip read-only fields that NPM rejects on PUT.
writable_only() {
    echo "$1" | jq 'del(.id, .created_on, .modified_on, .owner_user_id,
                        .nginx_online, .nginx_err, .owner)'
}

# Return writable object with top-level and all per-location forward_host set to $ip.
flip_to_laptop_ip() {
    local writable="$1" ip="$2"
    echo "$writable" | jq --arg ip "$ip" '
        .forward_host = $ip
        | .locations = ((.locations // []) | map(.forward_host = $ip))'
}

# --- Commands -----------------------------------------------------------------

cmd_status() {
    local token
    token="$(get_token)"
    for id in "$NPM_HEALTHBRIDGE_PROXY_ID" "$NPM_MCP_PROXY_ID"; do
        local proxy top locations
        proxy="$(read_proxy "$id" "$token")"
        top="$(echo "$proxy" | jq -r '.forward_host')"
        locations="$(echo "$proxy" | jq -r \
            '[.locations // [] | .[] | "\(.path) → \(.forward_host):\(.forward_port)"] | join(", ")')"
        echo "proxy ${id}: forward_host=${top}  locations=[${locations:-none}]"
    done
}

cmd_laptop() {
    local ip="${1:-}"
    [[ -n "$ip" ]] || die "usage: npm-flip.sh laptop <ip>"

    local token rollback
    token="$(get_token)"
    rollback='{}'

    # Read both proxies; save full writable state for rollback.
    for id in "$NPM_HEALTHBRIDGE_PROXY_ID" "$NPM_MCP_PROXY_ID"; do
        local raw writable
        raw="$(read_proxy "$id" "$token")"
        writable="$(writable_only "$raw")"
        rollback="$(echo "$rollback" | jq --arg id "$id" --argjson obj "$writable" '.[$id] = $obj')"
    done

    if [[ "$DRY_RUN" == "1" ]]; then
        log "DRY_RUN — rollback object:"
        echo "$rollback" | jq .
        return
    fi

    echo "$rollback" > "$LOCAL_ROLLBACK"
    log "rollback saved → $LOCAL_ROLLBACK"

    # Flip each proxy: top-level forward_host + all per-location forward_hosts.
    for id in "$NPM_HEALTHBRIDGE_PROXY_ID" "$NPM_MCP_PROXY_ID"; do
        local writable flipped
        writable="$(echo "$rollback" | jq --arg id "$id" '.[$id]')"
        flipped="$(flip_to_laptop_ip "$writable" "$ip")"
        put_proxy "$id" "$token" "$flipped" > /dev/null
        log "proxy ${id} → ${ip}"
    done
}

cmd_prod() {
    [[ -f "$LOCAL_ROLLBACK" ]] \
        || die "no rollback at $LOCAL_ROLLBACK — run 'laptop <ip>' first"

    local token rollback
    token="$(get_token)"
    rollback="$(cat "$LOCAL_ROLLBACK")"

    for id in "$NPM_HEALTHBRIDGE_PROXY_ID" "$NPM_MCP_PROXY_ID"; do
        local saved saved_type
        saved="$(echo "$rollback" | jq --arg id "$id" '.[$id] // empty')"
        [[ -n "$saved" ]] \
            || { echo "npm-flip: no entry for proxy ${id} in rollback — skipping" >&2; continue; }

        saved_type="$(echo "$saved" | jq -r 'type')"
        if [[ "$saved_type" == "string" ]]; then
            # Legacy rollback: value is a plain forward_host string — patch current object.
            local cur patched host
            host="$(echo "$saved" | jq -r .)"
            cur="$(writable_only "$(read_proxy "$id" "$token")")"
            patched="$(echo "$cur" | jq --arg h "$host" '.forward_host = $h')"
            put_proxy "$id" "$token" "$patched" > /dev/null
            log "proxy ${id} restored (legacy) → ${host}"
        else
            put_proxy "$id" "$token" "$saved" > /dev/null
            log "proxy ${id} restored → $(echo "$saved" | jq -r '.forward_host')"
        fi
    done

    rm -f "$LOCAL_ROLLBACK"
    log "rollback file removed"
}

# Parse "2 hours" / "30 minutes" / "90m" / raw seconds → integer seconds.
_duration_to_secs() {
    local d="$1"
    if [[ "$d" =~ ^[0-9]+$ ]]; then
        echo "$d"
    elif [[ "$d" =~ ^([0-9]+)[[:space:]]*(h|hour|hours)$ ]]; then
        echo $(( BASH_REMATCH[1] * 3600 ))
    elif [[ "$d" =~ ^([0-9]+)[[:space:]]*(m|min|minute|minutes)$ ]]; then
        echo $(( BASH_REMATCH[1] * 60 ))
    elif [[ "$d" =~ ^([0-9]+)[[:space:]]*(s|sec|second|seconds)$ ]]; then
        echo "${BASH_REMATCH[1]}"
    else
        die "unrecognised duration: '$d' (e.g. '2 hours', '30 minutes', '7200')"
    fi
}

# Normalise duration to "N unit" string suitable for `at now + N unit`.
_duration_for_at() {
    local d="$1"
    if [[ "$d" =~ ^[0-9]+$ ]]; then
        echo "$(( (d + 59) / 60 )) minutes"
    elif [[ "$d" =~ ^([0-9]+)[[:space:]]*(h|hour|hours)$ ]]; then
        echo "${BASH_REMATCH[1]} hours"
    elif [[ "$d" =~ ^([0-9]+)[[:space:]]*(m|min|minute|minutes)$ ]]; then
        echo "${BASH_REMATCH[1]} minutes"
    else
        # Pass through as-is; let `at` parse it on the remote host.
        echo "$d"
    fi
}

cmd_schedule_watchdog() {
    local duration="${1:?usage: npm-flip.sh schedule-watchdog <duration>}"
    local secs
    secs="$(_duration_to_secs "$duration")"

    # 1. Local background watchdog — covers accidents (not laptop crashes).
    local pid_file="/tmp/healthbridge-watchdog.pid"
    if [[ -f "$pid_file" ]] && kill -0 "$(cat "$pid_file")" 2>/dev/null; then
        kill "$(cat "$pid_file")" 2>/dev/null || true
        log "cancelled previous local watchdog (PID $(cat "$pid_file"))"
    fi
    (
        sleep "$secs"
        cd "$REPO_ROOT"
        if ./scripts/dev/npm-flip.sh prod; then
            log "local watchdog fired — NPM restored to prod"
        else
            echo "npm-flip: local watchdog: prod restore failed (try manually)" >&2
        fi
    ) &
    echo $! > "$pid_file"
    log "local watchdog PID $! set for ${duration} (${secs}s)"

    # 2. Remote watchdog — survives laptop crash. Needs NPM_WATCHDOG_SSH in .env.dev.
    if [[ -z "${NPM_WATCHDOG_SSH:-}" ]]; then
        log "NPM_WATCHDOG_SSH not set — remote watchdog skipped (local-only fallback active)"
        return
    fi
    _schedule_remote_watchdog "$duration"
}

_schedule_remote_watchdog() {
    local duration="$1"
    local at_duration remote_rollback remote_dir watchdog_script conf_file

    at_duration="$(_duration_for_at "$duration")"
    remote_rollback="${NPM_ROLLBACK_PATH:-/var/lib/healthbridge-dev/rollback.json}"
    remote_dir="$(dirname "$remote_rollback")"
    watchdog_script="$remote_dir/watchdog.sh"
    conf_file="$remote_dir/watchdog.conf"

    log "uploading rollback + watchdog to ${NPM_WATCHDOG_SSH}:${remote_dir} …"

    # shellcheck disable=SC2029
    ssh "$NPM_WATCHDOG_SSH" "mkdir -p '${remote_dir}'"

    # Upload the rollback JSON and watchdog script.
    scp -q "$LOCAL_ROLLBACK" "${NPM_WATCHDOG_SSH}:${remote_rollback}"
    scp -q "$REPO_ROOT/scripts/dev/watchdog.sh" "${NPM_WATCHDOG_SSH}:${watchdog_script}"

    # Write the config file (credentials) on the remote host via stdin pipe.
    printf 'NPM_BASE_URL=%s\nNPM_API_USER=%s\nNPM_API_PASSWORD=%s\nNPM_ROLLBACK_PATH=%s\n' \
        "$NPM_BASE_URL" "$NPM_API_USER" "$NPM_API_PASSWORD" "$remote_rollback" \
        | ssh "$NPM_WATCHDOG_SSH" "cat > '${conf_file}'; chmod 600 '${conf_file}'; chmod +x '${watchdog_script}'"

    # Cancel any previous at job, then schedule a new one.
    # shellcheck disable=SC2029
    ssh "$NPM_WATCHDOG_SSH" "
set -e
ATJOB_FILE='${remote_dir}/watchdog.atjob'
if [ -f \"\$ATJOB_FILE\" ]; then
    atrm \"\$(cat \"\$ATJOB_FILE\")\" 2>/dev/null || true
fi
JOB=\$(echo '${watchdog_script} ${conf_file}' | at now + ${at_duration} 2>&1 | awk '/^job /{print \$2}')
echo \"\$JOB\" > \"\$ATJOB_FILE\"
echo \"remote watchdog job \$JOB scheduled (now + ${at_duration})\"
"
    log "remote watchdog on ${NPM_WATCHDOG_SSH} scheduled for '${duration}' (at now + ${at_duration})"
}

case "${1:-}" in
    laptop)            shift; cmd_laptop "$@" ;;
    prod)              cmd_prod ;;
    status)            cmd_status ;;
    schedule-watchdog) shift; cmd_schedule_watchdog "$@" ;;
    *) die "usage: laptop <ip> | prod | status | schedule-watchdog <duration>" ;;
esac
