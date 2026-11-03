FROM dhi.io/python:3.13-dev@sha256:5e3256de9b2752db25f3432b7be761cef508fe97e70fed6184bc528100ace7ea AS builder

# One Python for every image: the -dev variant of the runtime base, with uv copied in.
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra infra --no-install-project \
    && mkdir -p /opt/dagster/dagster_home /app/compute_logs

FROM dhi.io/python:3.13@sha256:43b01c42d91a2e2ae676f974778518661c2fa97cb2b85c3fc737f75545524eb9

# The Dagster Helm chart starts its pods with /bin/bash -c.
COPY --from=builder /usr/bin/bash /usr/bin/bash
# The chart's DAGSTER_HOME, writable for the non-root user.
COPY --from=builder --chown=65532:65532 /opt/dagster/dagster_home /opt/dagster/dagster_home
COPY --from=builder --chown=65532:65532 /app/compute_logs /app/compute_logs

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    DAGSTER_HOME="/app/config/dagster"

COPY config/dagster/ /app/config/dagster/

EXPOSE 3000
