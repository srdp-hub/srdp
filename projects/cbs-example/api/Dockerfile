FROM dhi.io/python:3.12-dev@sha256:9161f64c177d483fdc63478b50f9e2bd6c550a6d7330579bbe9feef58e674f6b AS builder

# One Python for every image: the -dev variant of the runtime base, with uv copied in.
COPY --from=ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff /uv /uvx /bin/
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app

# Platform library, for the DuckLake connection helper (srdp.io.ducklake).
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --locked --no-dev --no-editable --extra ducklake --extra api

# The runtime stage doesn't need uv.
FROM dhi.io/python:3.12@sha256:71d4576b1118498ce4de72e9bdf8be7d2f4d368f05c329504041f2b8c100ccf1

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:${PATH}"

# The project's own API add-on: mounts its router onto the platform's core app.
COPY projects/cbs-example/api/app.py app.py

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
