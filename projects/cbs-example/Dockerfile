FROM dhi.io/python:3.12-dev@sha256:9161f64c177d483fdc63478b50f9e2bd6c550a6d7330579bbe9feef58e674f6b AS builder

# One Python for every image: the -dev variant of the runtime base, with uv copied in.
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/

# dagster-postgres isn't imported by any project code, but the code server still
# needs it importable: launching a run means it deserializes the platform's
# DagsterInstance, which references Postgres-backed storage.
RUN uv sync --locked --no-dev --no-editable --extra ducklake --extra dbt --extra dagster-postgres --extra openlineage \
    && mkdir -p /opt/dagster/dagster_home

COPY projects/cbs-example/ projects/cbs-example/
RUN mkdir -p projects/cbs-example/dbt/target projects/cbs-example/dbt/logs

# The runtime stage doesn't need uv.
FROM dhi.io/python:3.12@sha256:71d4576b1118498ce4de72e9bdf8be7d2f4d368f05c329504041f2b8c100ccf1

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/projects/cbs-example/src projects/cbs-example/src
# dbt writes its manifest and logs next to the project.
COPY --from=builder --chown=65532:65532 /app/projects/cbs-example/dbt projects/cbs-example/dbt
COPY --from=builder --chown=65532:65532 /opt/dagster/dagster_home /opt/dagster/dagster_home

ENV DAGSTER_HOME=/opt/dagster/dagster_home
ENV PYTHONPATH="/app/projects/cbs-example/src"
# The venv is on PATH so the k8s chart can exec `dagster` directly.
ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 3030

CMD ["dagster", "code-server", "start", "-h", "0.0.0.0", "-p", "3030", "-m", "etl.definitions"]
