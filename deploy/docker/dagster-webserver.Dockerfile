FROM dhi.io/python:3.12-dev@sha256:9161f64c177d483fdc63478b50f9e2bd6c550a6d7330579bbe9feef58e674f6b AS builder

# One Python for every image: the -dev variant of the runtime base, with uv copied in.
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra infra --no-install-project \
    && mkdir -p /opt/dagster/dagster_home

FROM dhi.io/python:3.12@sha256:71d4576b1118498ce4de72e9bdf8be7d2f4d368f05c329504041f2b8c100ccf1

# The Dagster Helm chart starts its pods with /bin/bash -c.
COPY --from=builder /usr/bin/bash /usr/bin/bash
# The chart's DAGSTER_HOME, writable for the non-root user.
COPY --from=builder --chown=65532:65532 /opt/dagster/dagster_home /opt/dagster/dagster_home

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    DAGSTER_HOME="/app/config/dagster"

COPY config/dagster/ /app/config/dagster/

EXPOSE 3000
