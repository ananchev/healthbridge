# Networking & topology

This is the established, zero-risk setup (same shape as cycling-coach).

```
Internet
  → Cloudflare (DNS proxy, orange cloud)        # DNS + edge only. NO CF Access, NO Tunnel.
  → router :443 port-forward
  → NPM container (reverse proxy, on the LAN)
  → <LAN-IP>:<published-port>                    # the upstream NPM forwards to
```

## The upstream is ALWAYS a LAN IP:port

NPM runs as a container but does **not** share a Docker network with the app
containers — by design, because there are multiple Docker hosts. So NPM reaches
every backend over the LAN by `IP:port`. The app containers **publish** their ports
on their Docker host's LAN interface.

- **Dev:**  NPM upstream → `your-laptop-LAN-IP:8000` (uvicorn running on the laptop).
- **Prod:** NPM upstream → `docker-host-LAN-IP:8000` (the backend container's host).

Because both are just LAN IPs, the dev↔prod switch is a uniform NPM "flip" with no
container-name special case. The public hostname never changes — only NPM's
upstream IP does. Cloudflare is untouched by a flip.

## Hostnames (stable across dev and prod)

- `healthbridge.example.com`     → NPM → `<LAN-IP>:8000`  (ingest API; the phone posts here)
- `mcp-healthbridge.example.com` → NPM → `<LAN-IP>:8001`  (sleep MCP; coach/Claude reach here)

A third host, `mcp-auth.example.com`, fronts the shared OAuth Authorization Server
(separate `mcp-auth` repo/stack) that protects the MCP — see `docs/MCP_AUTH.md`.

The ingestion client (Health Auto Export) always targets
`https://healthbridge.example.com` — identical in dev and prod. In dev, NPM is
flipped to the laptop, so that hostname reaches local uvicorn.

## Auth

Cloudflare here is DNS-only and does not authenticate. NPM does not authenticate
the API routes either. Auth differs by service:

- **Ingest API (backend):** the **backend owns auth** via a bearer token (`auth.py`):
  the phone sends `Authorization: Bearer <HEALTHBRIDGE_TOKEN>`. `/health` is open
  (liveness); `/ingest`, `/ingest/hae`, `/stats` require the token.
- **sleep-mcp:** an **OAuth 2.1 Resource Server**. Clients (Claude) authenticate
  against the shared `mcp-auth` AS and present a bearer JWT, which sleep-mcp verifies
  with the shared `MCP_OAUTH_SIGNING_KEY`. Not the backend token. See `docs/MCP_AUTH.md`.

## Monitoring dashboard — LAN-only, NOT public

The backend serves a read-only monitoring UI at `/dashboard` (+ its data feed
`/dashboard/data`). These routes are **deliberately unauthenticated** and are meant
to be reached **directly on the LAN** at `http://<backend-LAN-IP>:8000/dashboard`,
bypassing Cloudflare and NPM entirely. They are read-only (SELECTs against
`nightly_summary`); the single-writer rule and the `/ingest` bearer auth are
unchanged.

**They must NOT be exposed on the public hostname.** Because the public proxy host
forwards `healthbridge.example.com` → backend `:8000`, `/dashboard*` would otherwise
be world-reachable. Block it at NPM on that proxy host (see `deploy/NPM.md` →
"Block the dashboard on the public host") so the only path to the UI is the LAN IP.

## Firewall

- Only the router's :443 → NPM forward is public.
- The app ports (8000/8001) stay LAN-internal — never port-forwarded on the router.
- If a Docker host runs ufw/nftables, allow the NPM container's host to reach
  8000/8001 on that host. Optionally bind published ports to the LAN IP only
  (compose: `"<LAN-IP>:8000:8000"`) so they aren't on every interface.

## Dev vs prod, concretely

| | Public hostname | CF | NPM upstream | Backend runs as |
|--|--|--|--|--|
| **Dev (e2e on laptop)** | healthbridge.example.com | DNS proxy | laptop-LAN-IP:8000 | uvicorn in a dedicated venv |
| **Prod** | healthbridge.example.com | DNS proxy | docker-host-LAN-IP:8000 | container (compose, published port) |

The flip (`scripts/dev/npm-flip.sh`) moves the NPM upstream between these two LAN
IPs. It is part of the **dev** loop from day one, not a prod-only tool.
