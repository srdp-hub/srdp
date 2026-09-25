FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Copy dependency definition and lockfile first for layer caching
COPY pyproject.toml uv.lock README.md ./

# Copy platform library
COPY src/ src/

# Copy the ETL project
COPY projects/cbs-example/ projects/cbs-example/

# Install all deps (including srdp in editable mode)
# dagster-postgres isn't imported by any project code, but the code server still
# needs it importable: launching a run means it deserializes the platform's
# DagsterInstance, which references Postgres-backed storage.
RUN uv sync --frozen --no-dev --extra ducklake --extra dbt --extra dagster-postgres --extra openlineage

ENV DAGSTER_HOME=/opt/dagster/dagster_home
ENV PYTHONPATH="/app/projects/cbs-example/src:${PYTHONPATH}"
# Put the venv on PATH so the k8s chart can exec `dagster` directly (it bypasses `uv run`)
ENV PATH="/app/.venv/bin:${PATH}"
RUN mkdir -p "${DAGSTER_HOME}"

EXPOSE 3030

CMD ["uv", "run", "dagster", "code-server", "start", \
     "-h", "0.0.0.0", "-p", "3030", "-m", "etl.definitions"]
