FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS builder

# One Python for every image: the same base as the runtime stages, with uv copied in.
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --extra ducklake --no-editable

# The runtime stage doesn't need uv.
FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

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
