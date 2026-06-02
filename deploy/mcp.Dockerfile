FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY sleep_mcp ./sleep_mcp

RUN pip install --no-cache-dir .

VOLUME ["/data"]
EXPOSE 8001

# Adjust transport/port to match how your cycling-coach MCP is served
# (stdio vs HTTP/SSE). FastMCP supports multiple transports.
CMD ["python", "-m", "sleep_mcp.server"]
