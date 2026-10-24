FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

# One Python for every image: the same base as the runtime stages, with uv copied in.
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

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
