FROM dhi.io/python:3.13-dev@sha256:5e3256de9b2752db25f3432b7be761cef508fe97e70fed6184bc528100ace7ea AS builder

# One Python for every image: the -dev variant of the runtime base, with uv copied in.
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --locked --no-dev --extra ducklake --no-editable \
    && mkdir -p /data/ducklake

# The runtime stage doesn't need uv.
FROM dhi.io/python:3.13@sha256:43b01c42d91a2e2ae676f974778518661c2fa97cb2b85c3fc737f75545524eb9

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
# srdp-setup is the first to mount the ducklake-data volume, which inherits this ownership.
COPY --from=builder --chown=65532:65532 /data/ducklake /data/ducklake

ENV PATH="/app/.venv/bin:${PATH}"

ENTRYPOINT ["python", "-m", "srdp.setup"]
