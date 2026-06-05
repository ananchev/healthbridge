# Local development & e2e (CF → NPM → laptop)

Same approach as cycling-coach, and it's the PRIMARY dev/e2e path — not a prod-only
tool. Run the real backend + MCP on your laptop (dedicated venv, uvicorn, no
Docker), and flip the NPM upstreams to your laptop's LAN IP. The full public path
— Cloudflare (DNS proxy) → router :443 → NPM → laptop — then hits local code, so
the ingestion client (Health Auto Export) talks to the same
`https://healthbridge.example.com` it will use in prod.
Real bearer-token auth is exercised (do NOT set HEALTHBRIDGE_DEV on this path).
NPM auto-reverts when you exit. See `deploy/NETWORKING.md` for the topology.

## One-time setup

1. `cp .env.dev.example .env.dev` and fill in NPM connection details + proxy IDs.
2. In NPM, note the proxy-host IDs for `healthbridge.example.com` and `mcp-sleep.example.com`.
   Their normal forward target is the **Docker host's LAN IP** (where the
   containers run); the flip swaps it to your laptop's LAN IP and back.
3. Create a dedicated NPM API user for dev flips (least privilege).

## Daily flow

```sh
# Flip NPM to laptop, spawn backend+mcp in a screen session, schedule watchdog
./scripts/dev/start-dev-stack.sh

# Backend only (skip the MCP window)
./scripts/dev/start-dev-stack.sh --no-mcp

# Run locally WITHOUT touching NPM (process wiring only — hit localhost:8000
# directly; set HEALTHBRIDGE_DEV=1 yourself to bypass auth on this path)
./scripts/dev/start-dev-stack.sh --no-flip

# Force a specific laptop IP
./scripts/dev/start-dev-stack.sh --ip 192.168.1.42

# Tighter watchdog ceiling
./scripts/dev/start-dev-stack.sh --max-runtime "30 minutes"
```

After it attaches:
- `Ctrl-A 0/1` switch between `backend` / `mcp` windows
- `Ctrl-A d` detach → launcher keeps running → on exit NPM auto-reverts
- `screen -r healthbridge-dev` re-attach from another terminal
- `Ctrl-C` in the launcher tears everything down and reverts NPM

## The APP_ENV banner

When `APP_ENV` is not `prod`, the backend's `/stats` (and any admin surface)
renders an amber banner + `[env]` title prefix so a flipped local session is
unmistakable from a real prod tab at the same URL. `start-dev-stack.sh` sets it to
`dev hosted from <hostname>` (hostname, not IP, to dodge privacy-extension WebRTC
stripping — learned in cycling-coach).

## Files

| File | Purpose |
|--|--|
| `start-dev-stack.sh` | Launcher: env, IP detect, NPM flip, screen, watchdog, trap-revert |
| `npm-flip.sh` | NPM upstream CLI (laptop / prod / status / schedule-watchdog) |
| `watchdog.sh` | Runs on the NPM host; reverts NPM after a deadline if the dev session dies |
| `watchdog-deploy.md` | How to install `watchdog.sh` on the NPM host |

## Safety properties (carried from cycling-coach, do not regress)

- Rollback stores the **full writable proxy object**, not just `forward_host`.
- Flip mutates the top-level forward_host **and every location override**, so a
  per-path rule (e.g. `/stats`) can't silently route to prod while "flipped".
- `cmd_prod` is backward-compatible with the legacy string rollback format.
- The watchdog is a **fallback**: even if the laptop crashes mid-session, NPM
  reverts on the host-side deadline.
