FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS builder

# One Python for every image: the same base as the runtime stages, with uv copied in.
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --extra ducklake --no-editable

# The runtime stage doesn't need uv.
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

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
