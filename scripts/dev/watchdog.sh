#!/usr/bin/env bash
# scripts/dev/watchdog.sh — runs ON THE NPM HOST. Fallback auto-revert.
#
# Scheduled by `npm-flip.sh schedule-watchdog`. If the dev session dies without
# cleanly reverting NPM (laptop crash, network drop), this fires after the deadline
# and forces NPM back to prod upstreams. Idempotent: a clean exit cancels it.
#
# NOTE for Claude Code: implement using `at` (or systemd-run --on-active) to invoke
# the prod-restore against the NPM API after <duration>. Mirror cycling-coach's
# watchdog.sh. Support running inside a docker exec if NPM is containerized
# (NPM_DOCKER_CONTAINER in env).
set -euo pipefail
echo "watchdog: stub — implement per scripts/dev/watchdog-deploy.md"
