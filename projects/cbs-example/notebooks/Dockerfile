FROM python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258

# One Python for every image: the same base as the runtime stages, with uv copied in.
COPY --from=ghcr.io/astral-sh/uv:0.12.19@sha256:04d046b13e60d6bcec73cbc5e1cad25d680dea90c8573340950a0ac2d1aef424 /uv /uvx /bin/
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
