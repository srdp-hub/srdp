FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Platform library, for srdp.api.main's create_app and the DuckLake connection helper.
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --extra ducklake --extra api

ENV PATH="/app/.venv/bin:${PATH}"

# The project's own API add-on: mounts its router onto the platform's core app.
COPY projects/cbs-example/api/app.py app.py

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
