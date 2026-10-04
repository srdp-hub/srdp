FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --extra ducklake --no-editable

# The runtime stage doesn't need uv, and the plain Python image gets OS
# security fixes sooner than the uv image.
FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends haproxy \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}"

COPY services/duckdb-ui/serve.py serve.py
COPY services/duckdb-ui/haproxy.cfg haproxy.cfg
COPY services/duckdb-ui/entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh

EXPOSE 4213

ENTRYPOINT ["./entrypoint.sh"]
