FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY healthbridge ./healthbridge

RUN pip install --no-cache-dir .

# /data is a mounted volume holding health.duckdb
VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "healthbridge.app:app", "--host", "0.0.0.0", "--port", "8000"]
