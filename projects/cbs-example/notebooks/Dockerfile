FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim@sha256:e5b65587bce7de595f299855d7385fe7fca39b8a74baa261ba1b7147afa78e58

WORKDIR /app

# Platform library, for the DuckLake connection helper (srdp.io.ducklake) — this is
# what lets the notebook attach the same catalog Dagster and dbt write to.
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
# The app tool comes from the pyproject dependency group of the same name.
RUN uv sync --frozen --no-dev --extra ducklake --group marimo

ENV PATH="/app/.venv/bin:${PATH}"

COPY projects/cbs-example/notebooks/app.py notebooks/app.py

EXPOSE 8000

CMD ["marimo", "run", "notebooks/app.py", "--host", "0.0.0.0", "--port", "8000"]
