FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends haproxy \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --extra ducklake

ENV PATH="/app/.venv/bin:${PATH}"

COPY services/duckdb-ui/serve.py serve.py
COPY services/duckdb-ui/haproxy.cfg haproxy.cfg
COPY services/duckdb-ui/entrypoint.sh entrypoint.sh
RUN chmod +x entrypoint.sh

EXPOSE 4213

ENTRYPOINT ["./entrypoint.sh"]
