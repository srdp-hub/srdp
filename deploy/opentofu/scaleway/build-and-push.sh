#!/bin/sh

SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"

# Configuration
REGISTRY="rg.nl-ams.scw.cloud/srdp-registry"
VERSION="v1.0"

echo "Logging into Scaleway Registry"
echo "$SCW_SECRET_KEY" | docker login rg.nl-ams.scw.cloud -u nologin --password-stdin

echo "Building and Pushing SRDP Images"
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

echo "Building Quarto..."
docker build --platform linux/amd64 -t "$REGISTRY/quarto:$VERSION" "$REPO_ROOT/services/quarto"
docker push "$REGISTRY/quarto:$VERSION"

echo "Building SRDP ETL (Dagster user code)..."
# Build context is repo root — the Dockerfile needs access to src/ and projects/
docker build --platform linux/amd64 \
  -f "$REPO_ROOT/projects/cbs-example/Dockerfile" \
  -t "$REGISTRY/srdp-etl:$VERSION" \
  "$REPO_ROOT"
docker push "$REGISTRY/srdp-etl:$VERSION"

echo "Done! Images pushed."
