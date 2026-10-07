#!/bin/sh
set -e

SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"

# Configuration. REGISTRY comes from srdp.toml [deploy] via `just build-and-push`.
REGISTRY="${REGISTRY:?set REGISTRY, or run via just build-and-push}"
VERSION="${VERSION:-$(cd "$REPO_ROOT" && uv run --no-project python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')}"

echo "Logging into Scaleway Registry"
echo "$SCW_SECRET_KEY" | docker login "${REGISTRY%%/*}" -u nologin --password-stdin

echo "Building and Pushing SRDP Images (the platform images come from ghcr.io/srdp-hub)"
echo "Target Registry: $REGISTRY"
echo "Version: $VERSION"

echo "Building Marimo..."
# Build context is repo root — the Dockerfile needs access to src/ and the
# CBS-specific notebook under projects/cbs-example/.
docker build --platform linux/amd64 \
  -f "$REPO_ROOT/projects/cbs-example/notebooks/Dockerfile" \
  -t "$REGISTRY/marimo:$VERSION" \
  "$REPO_ROOT"
docker push "$REGISTRY/marimo:$VERSION"

echo "Building SRDP ETL (Dagster user code)..."
# Build context is repo root — the Dockerfile needs access to src/ and projects/
docker build --platform linux/amd64 \
  -f "$REPO_ROOT/projects/cbs-example/Dockerfile" \
  -t "$REGISTRY/srdp-etl:$VERSION" \
  "$REPO_ROOT"
docker push "$REGISTRY/srdp-etl:$VERSION"

echo "Building SRDP API..."
docker build --platform linux/amd64 \
  -f "$REPO_ROOT/projects/cbs-example/api/Dockerfile" \
  -t "$REGISTRY/srdp-api:$VERSION" \
  "$REPO_ROOT"
docker push "$REGISTRY/srdp-api:$VERSION"

echo "Building Streamlit..."
docker build --platform linux/amd64 \
  -f "$REPO_ROOT/projects/cbs-example/streamlit/Dockerfile" \
  -t "$REGISTRY/streamlit:$VERSION" \
  "$REPO_ROOT"
docker push "$REGISTRY/streamlit:$VERSION"

echo "Done! Images pushed."
