FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Platform library, for the DuckLake connection helper (srdp.io.ducklake) — this is
# what lets the notebook attach the same catalog Dagster and dbt write to.
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --extra ducklake

# Marimo itself is a runtime tool, not a dependency of the srdp library.
RUN uv pip install --python .venv/bin/python "marimo>=0.13"

ENV PATH="/app/.venv/bin:${PATH}"

COPY projects/cbs-example/notebooks/app.py notebooks/app.py

EXPOSE 8000

CMD ["marimo", "run", "notebooks/app.py", "--host", "0.0.0.0", "--port", "8000"]
