# Security Posture

HealthBridge ingests private health data over a public hostname
(`healthbridge.example.com`). Defense is layered — edge, reverse proxy, and app — so no
single layer is the only thing standing between the internet and the data.

```
Internet
  │
  ▼  Cloudflare (DNS proxy / orange cloud) — hides origin IP, terminates TLS,
  │   optional WAF + rate limiting. NO Cloudflare Access, NO Tunnel.
  ▼  Router :443 → NPM (reverse proxy)
  │   • IP allowlist: `allow <Cloudflare CIDRs>; deny all; satisfy all`
  │     → only Cloudflare-proxied traffic can reach the origin at all.
  │   • client_max_body_size cap (defense-in-depth with the app's own guard).
  ▼  Backend (FastAPI) on the LAN — owns authentication and app hardening.
```

## Edge (Cloudflare)

- DNS proxy on: the origin LAN IP is never exposed publicly; direct-to-origin scans
  can't find it.
- TLS terminated at the edge; HSTS is an edge concern (set there, not at the origin).
- Recommended: a Cloudflare rate-limiting rule on `/ingest*` (app-layer rate limiting
  is intentionally NOT in the backend — it belongs at the edge).

## Reverse proxy (NPM)

- Both proxy hosts carry a Cloudflare IP allowlist in the nginx location block
  (`allow <CF-CIDR>; deny all; satisfy all`). Traffic that didn't come through
  Cloudflare is refused before it reaches the app. This is the practical perimeter.
- Set `client_max_body_size` (e.g. `25m`) to match the app's `HEALTHBRIDGE_MAX_BODY_BYTES`
  so oversized uploads die at the proxy.

## Application (FastAPI) — `backend/healthbridge/`

- **Bearer-token auth** on all data paths (`/ingest`, `/ingest/hae`, `/stats`):
  constant-time compare (`hmac.compare_digest`), 401 on missing/malformed header,
  403 on mismatch, 500 if the token is unconfigured (fail closed). See `auth.py`.
  `HEALTHBRIDGE_DEV=1` bypass is for the localhost-direct path ONLY — never in prod.
- **OpenAPI schema and interactive docs disabled** (`docs_url`/`redoc_url`/`openapi_url`
  = None): the endpoint/payload surface is not enumerable.
- **Root path `/`** is an open liveness/preflight handler returning only
  `{"status":"ok"}` — no app name, version, or framework details. (Health Auto Export
  POSTs the host root as a reachability check before sending data.)
- **Request-body size guard**: requests declaring `Content-Length` over
  `HEALTHBRIDGE_MAX_BODY_BYTES` (default 25 MB) are rejected with 413 before parsing —
  memory-exhaustion protection on the public POST surface.
- **Security headers** on every response: `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `Cache-Control: no-store` (health data is never
  cached by intermediaries), `X-Frame-Options: DENY`.
- **Server banner stripped** (`--no-server-header`); Cloudflare masks it at the edge
  regardless.
- **No CORS**: there is no browser client, so cross-origin access stays disabled.
- **LAN-only dashboard, fail-closed**: `/dashboard` (+ `/dashboard/data`) is an
  unauthenticated read-only monitoring UI. `_require_lan` serves it ONLY to direct LAN
  requests — anything carrying proxy forwarding headers (`X-Forwarded-For` etc., stamped
  by NPM on all proxied traffic) gets 404. Since `:8000` is never internet-reachable,
  the public side can't reach it regardless of the NPM rule or this repo being public
  (no security-by-obscurity). `HEALTHBRIDGE_DASHBOARD_PUBLIC=1` disables the guard —
  set it ONLY if you front the dashboard with your own auth. NPM also blocks
  `^~ /dashboard` (return 404) as defense-in-depth. See `deploy/NPM.md`.
- **Single writer / read-only readers**: only `backend/` opens the DB read-write; MCP
  and the coach open it `read_only=True`.

## Secrets

- `HEALTHBRIDGE_TOKEN` and all credentials come from environment / `.env` files that
  are gitignored (`.env`, `.env.*`). Personal exports (`HealthAutoExport-*.json`,
  `export.zip`) are gitignored too. Generate the token with `openssl rand -hex 32`.

## Threats explicitly NOT handled here

- Multi-user authorization / RBAC — single-user system, one shared token.
- App-layer rate limiting / brute-force lockout — delegated to Cloudflare/NPM.
- At-rest encryption of the DuckDB file — rely on host disk encryption.
