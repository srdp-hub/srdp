FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58 AS builder

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --extra ducklake --no-editable

# The runtime stage doesn't need uv, and the plain Python image gets OS
# security fixes sooner than the uv image.
FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83

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
