# MCP Authentication — shared AS + sleep-mcp Resource Server

`sleep-mcp` is a claude.ai web connector, so it needs OAuth 2.1. Auth is split the
standard way — **one Authorization Server (AS), many Resource Servers (RS)**:

```
claude.ai ──OAuth (DCR + PKCE)──▶  mcp-auth  (AS, separate repo ~/Development/mcp-auth)
          ◀──── HS256 access + refresh tokens ────┘   issuer: https://mcp-auth.example.com
   │
   └──Bearer JWT──▶ sleep-mcp (RS, mcp/)  verifies HS256 with the SAME signing key,
                    advertises the AS via RFC 9728, serves the tools over HTTP :8001
                    public: https://mcp-healthbridge.example.com
```

The AS is the proven cycling-coach OAuth server carved into its own repo, plus
rotating **refresh tokens**. It is reused (not duplicated): cycling-coach will later
repoint at it too. Tokens are HS256 with claims `{sub, iss, iat, exp}` and **no
audience** — any RS trusting the issuer + signing key accepts them.

## Token verification (RS side)

`mcp/sleep_mcp/server.py` uses FastMCP:
- `JWTVerifier(public_key=MCP_OAUTH_SIGNING_KEY, algorithm="HS256", issuer=MCP_AUTH_SERVER_URL)`
- `RemoteAuthProvider(token_verifier=…, authorization_servers=[MCP_AUTH_SERVER_URL], base_url=MCP_PUBLIC_URL)`
- A Starlette CORS middleware (outermost) so browser preflight is answered before auth
  and `WWW-Authenticate` is exposed.

FastMCP serves these paths (note the exact, MCP-spec paths):
- MCP endpoint: `POST /mcp` (no trailing slash)
- Protected-resource metadata: `GET /.well-known/oauth-protected-resource/mcp`

## Environment

**AS** (`~/Development/mcp-auth/.env`): `MCP_PUBLIC_URL=https://mcp-auth.example.com`,
`MCP_OAUTH_SIGNING_KEY`, `MCP_OAUTH_USER`, `MCP_OAUTH_PASSWORD`,
`MCP_OAUTH_ALLOWED_REDIRECTS=https://claude.ai/api/mcp/auth_callback`,
`MCP_OAUTH_REFRESH_STORE=/data/refresh.json`. See that repo's `.env.example`.

**RS** (`sleep-mcp`):
| Var | Meaning |
|---|---|
| `HEALTHBRIDGE_DB` | DuckDB path (opened read-only) |
| `MCP_OAUTH_SIGNING_KEY` | **same** HS256 secret as the AS |
| `MCP_AUTH_SERVER_URL` | AS issuer, e.g. `https://mcp-auth.example.com` |
| `MCP_PUBLIC_URL` | this RS's public origin, e.g. `https://mcp-healthbridge.example.com` |
| `MCP_PORT` | listen port (default 8001) |
| `HEALTHBRIDGE_MCP_DEV=1` | disable auth for localhost-direct dev only |

## Topology

Same pattern as the backend: Cloudflare (DNS proxy) → NPM (Cloudflare-IP allowlist +
`deny all`) → LAN host. Two **single-label** hosts under `example.com` (Universal SSL
doesn't cover 2-level subdomains): `mcp-auth.example.com` (AS, new CF DNS + NPM proxy
needed) and `mcp-healthbridge.example.com` (RS, existing NPM proxy 12 → :8001).

## Validation checklist (run from outside — the Go/pytest suites are browser-blind)

```bash
# 1. AS discovery — every URL must be the public origin, grants include refresh_token
curl -s https://mcp-auth.example.com/.well-known/oauth-authorization-server | python3 -m json.tool

# 2. RS protected-resource metadata — resource = RS/mcp, authorization_servers = [AS]
curl -s https://mcp-healthbridge.example.com/.well-known/oauth-protected-resource/mcp | python3 -m json.tool

# 3. CORS preflight — 200/204 with Access-Control-Allow-Origin
curl -i -X OPTIONS https://mcp-healthbridge.example.com/mcp \
  -H 'Origin: https://claude.ai' -H 'Access-Control-Request-Method: POST'

# 4. Unauthenticated /mcp — 401 with WWW-Authenticate: ... resource_metadata="…"
curl -i -X POST https://mcp-healthbridge.example.com/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'
```

Then add sleep-mcp to claude.ai as a connector, complete OAuth, call `get_latest_night`.
After the access token's TTL, claude.ai should silently refresh (refresh-token grant).

## Local dev (no claude.ai, no public DNS)

Run the AS and RS on the laptop and mint a token without the browser flow:

```bash
# AS
cd ~/Development/mcp-auth
export MCP_OAUTH_SIGNING_KEY=dev MCP_OAUTH_USER=dev MCP_OAUTH_PASSWORD=dev \
       MCP_PUBLIC_URL=http://localhost:8092 MCP_OAUTH_REFRESH_STORE=/tmp/r.json
go run ./cmd/server &           # discovery + token endpoints on :8092
TOK=$(go run ./cmd/mint -pass dev)

# RS (auth on; same signing key + issuer)
cd ~/Development/healthbridge/mcp
HEALTHBRIDGE_DB=../backend/dev.duckdb MCP_OAUTH_SIGNING_KEY=dev \
  MCP_AUTH_SERVER_URL=http://localhost:8092 MCP_PUBLIC_URL=http://localhost:8001 \
  .venv-dev/bin/python -m sleep_mcp.server &

curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8001/mcp \
  -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'        # 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8001/mcp \
  -H "Authorization: Bearer $TOK" -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'        # 200
```

Or skip auth entirely for tool iteration: `HEALTHBRIDGE_MCP_DEV=1 python -m sleep_mcp.server`.
