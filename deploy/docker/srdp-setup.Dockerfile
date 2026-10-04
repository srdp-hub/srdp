FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --extra ducklake --no-editable

# The runtime stage doesn't need uv, and the plain Python image gets OS
# security fixes sooner than the uv image.
FROM python:3.12-slim-bookworm

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}"

USER nobody

ENTRYPOINT ["python", "-m", "srdp.setup"]
