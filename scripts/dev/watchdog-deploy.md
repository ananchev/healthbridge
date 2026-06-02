# Deploying the NPM-host watchdog

The watchdog is a host-side safety net that reverts NPM to prod upstreams if a
local dev session dies without cleaning up.

## If NPM runs directly on a host

1. Copy `watchdog.sh` to the NPM host (e.g. `/var/lib/healthbridge-dev/watchdog.sh`).
2. Ensure `at` (or systemd-run) is available.
3. `start-dev-stack.sh` → `npm-flip.sh schedule-watchdog "<duration>"` SSHes in and
   schedules `watchdog.sh` to run after the deadline, invoking the prod-restore.
4. A clean dev-session exit cancels the pending `at` job.

## If NPM runs in a Docker container

Set `NPM_DOCKER_CONTAINER=<name>` in `.env.dev`. The flip/restore commands wrap
NPM API calls so they originate from the right network context, and the watchdog
runs on the host but targets the container's API port.

## Least privilege

Use a dedicated NPM API user for dev flips. The watchdog only needs permission to
read + update the two proxy hosts (healthbridge, mcp-sleep).
