FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY sleep_mcp ./sleep_mcp

RUN pip install --no-cache-dir .

VOLUME ["/data"]
EXPOSE 8001

# Serves Streamable HTTP on 0.0.0.0:${MCP_PORT:-8001} via build_app() (FastMCP +
# CORS). Auth is enabled when MCP_OAUTH_SIGNING_KEY is set (see docker-compose.yml).
CMD ["python", "-m", "sleep_mcp.server"]
