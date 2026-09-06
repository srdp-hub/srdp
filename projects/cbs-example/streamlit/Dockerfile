FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Platform library, for the DuckLake connection helper (srdp.io.ducklake).
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --extra ducklake

# Streamlit itself is a runtime tool, not a dependency of the srdp library.
RUN uv pip install --python .venv/bin/python streamlit

ENV PATH="/app/.venv/bin:${PATH}"

COPY projects/cbs-example/streamlit/app.py app.py

EXPOSE 8000

CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "8000", "--server.headless", "true"]
