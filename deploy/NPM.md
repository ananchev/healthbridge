# Nginx-Proxy-Manager (NPM) setup

HealthBridge sits behind Cloudflare (DNS proxy / orange cloud) → router :443 →
your existing NPM container, same as cycling-coach. See `deploy/NETWORKING.md` for
the full topology. NPM reverse-proxies the two hostnames to a LAN IP:port (the
laptop in dev, a Docker host in prod). There is no Cloudflare Access; auth is the
backend's bearer token (plus an optional NPM Access List on `/stats`).

## Hostnames

- `healthbridge.example.com`     → `<docker-host-LAN-IP>:8000`   (ingest API; phone posts here)
- `mcp-healthbridge.example.com` → `<docker-host-LAN-IP>:8001`   (MCP server; coach/Claude reach here)

The shared OAuth AS has its own host `mcp-auth.example.com` and lives in the separate
`mcp-auth` repo/stack (its own deploy + NPM proxy). See `docs/MCP_AUTH.md`.

## Create the proxy hosts in NPM

For each hostname:
1. Hosts → Proxy Hosts → Add Proxy Host.
2. Domain: the hostname above. Scheme `http`, forward host =
   **the Docker host's LAN IP** (the machine running the HealthBridge containers,
   e.g. `192.168.1.50`), forward port `8000` (backend) / `8001` (sleep-mcp).
3. SSL tab: request a Let's Encrypt cert, force SSL, HTTP/2.
4. Note each proxy host's **ID** (visible in the URL when editing, or via the API).
   You need these for the dev-flip scripts: `NPM_HEALTHBRIDGE_PROXY_ID`,
   `NPM_MCP_PROXY_ID`.

## Auth model (no Cloudflare Access)

Because we're not using CF Access, the backend authenticates requests itself:

- **Phone → backend:** the app sends `Authorization: Bearer <HEALTHBRIDGE_TOKEN>`.
  The backend rejects anything without the matching token. The token lives in the
  phone's Keychain and in the backend's env (`HEALTHBRIDGE_TOKEN`).
- **Optional NPM Access List:** add an NPM Access List (Basic Auth or allow-by-IP)
  in front of `/stats` if you want browser access gated too. The `/ingest` route
  relies on the bearer token (the phone can't do interactive auth).
- **Defense in depth:** keep `/ingest` bearer-only; optionally restrict source IPs
  in NPM's advanced config if your phone has a stable egress (usually it won't).
- **sleep-mcp → OAuth, not the bearer token:** the MCP server is an OAuth 2.1
  Resource Server. Claude authenticates against the shared `mcp-auth` AS and presents
  a JWT that sleep-mcp verifies (shared `MCP_OAUTH_SIGNING_KEY`). Don't put an NPM
  Access List in front of `mcp-healthbridge.example.com` — it would break the OAuth/
  CORS handshake. See `docs/MCP_AUTH.md`.

This replaces the previous CF-Access-Authenticated-User-Email trust model. The
backend owns ingest auth via the bearer token (`backend/healthbridge/auth.py`); the
MCP owns its auth via OAuth.

## Networking model

NPM is a **container**, but it does NOT share a Docker network with the app
containers (you run multiple Docker hosts). So NPM reaches every upstream over the
LAN by `IP:port`. The HealthBridge `docker-compose.yml` publishes ports 8000/8001
on its Docker host's LAN interface, and each NPM proxy host forwards to
`http://<LAN-IP>:<port>` — the laptop's LAN IP in dev, a Docker host's LAN IP in
prod. The authoritative description (with the Cloudflare front and the dev/prod
table) is `deploy/NETWORKING.md`.

Set in `deploy/.env`: `BACKEND_PORT` / `MCP_PORT` (default 8000/8001). Optionally
bind to one interface in compose: `"<LAN-IP>:8000:8000"`.

**Firewall:** only the router's :443 → NPM forward is public. Keep 8000/8001 off the
router's forward list — LAN-internal only. If a Docker host runs ufw/nftables, allow
the NPM container's host to reach those ports.

## Dev flips

`scripts/dev/npm-flip.sh` talks to the NPM API to swap these proxies' upstreams
between prod and your laptop during local development. It stores a full-state
rollback and reverts on exit / via the host watchdog. See `scripts/dev/README.md`.

## Verify

```sh
# With token — should work
curl https://healthbridge.example.com/health \
  -H "Authorization: Bearer $HEALTHBRIDGE_TOKEN"

# Without token — backend returns 401
curl https://healthbridge.example.com/ingest -X POST -d '{}'
```
