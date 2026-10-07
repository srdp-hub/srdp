set shell := ["bash", "-c"]
set dotenv-load := false

namespace := "srdp"
# Registry prefix of every image this repo builds, from srdp.toml [deploy]. The
# deploy recipes pass it to the chart as global.srdpRegistry and as the Dagster
# code location's repository, a subchart value the chart cannot template.
registry := `uv run --no-project python -c 'import sys, tomllib; sys.stdout.write(tomllib.load(open("srdp.toml", "rb"))["deploy"]["registry"])'`
# Registry of the platform images SRDP publishes, the chart's global.platformRegistry.
platform_registry := "ghcr.io/srdp-hub"
# Helm patches deployments[0] in place only when a -f file defines the list
# (values.yaml or values-prod.yaml do), otherwise the --set replaces it.
registry_args := "--set-string 'global.srdpRegistry=" + registry + "' --set-string 'dagster.dagster-user-deployments.deployments[0].image.repository=" + registry + "/srdp-etl'"
# The chart rolls its own pods when a local Secret changes (srdp.localSecretsChecksum).
# Subcharts cannot hash the parent's Secrets, so local-deploy passes them a hash of
# values-local.yaml, which holds every local Secret value.
local_secrets_sum := `uv run --no-project python -c 'import hashlib, sys; sys.stdout.write(hashlib.sha256(open("deploy/kubernetes/srdp-chart/values-local.yaml", "rb").read()).hexdigest())'`
local_secrets_args := "--set-string 'oauth2-proxy.podAnnotations.checksum/local-secrets=" + local_secrets_sum + "' --set-string 'dagster.dagsterWebserver.annotations.checksum/local-secrets=" + local_secrets_sum + "' --set-string 'dagster.dagsterDaemon.annotations.checksum/local-secrets=" + local_secrets_sum + "' --set-string 'dagster.dagster-user-deployments.deployments[0].annotations.checksum/local-secrets=" + local_secrets_sum + "'"
# Staged prod rollout: apps that every partial stage leaves off.
prod_apps_off := "--set dagster.enabled=false --set marimo.enabled=false --set quarto.enabled=false --set streamlit.enabled=false --set api.enabled=false --set duckdbUi.enabled=false --set marquez.enabled=false --set setup.enabled=false"
# Traefik and the hub page only, to get the first LoadBalancer IP.
prod_traefik_only_args := "--set zitadel.enabled=false --set zitadel-db.enabled=false --set oauth2-proxy.enabled=false " + prod_apps_off
# Traefik, the hub page and the auth stack (Zitadel, its database, OAuth2-Proxy).
prod_auth_only_args := "--set zitadel.enabled=true --set oauth2-proxy.enabled=true " + prod_apps_off
kubeconfig := justfile_directory() + "/deploy/opentofu/scaleway/kubeconfig.yaml"

default: help

# List all available commands
help:
	@just --list

# ─── Scaleway landing zone (deploy/scaleway) ─────────────────────────────────

# Delegate to deploy/scaleway's own Justfile
scaleway *args:
	@just -f deploy/scaleway/Justfile -d deploy/scaleway {{args}}

# ─── Local development ────────────────────────────────────────────────────────

# Trust mkcert's local CA (no-op once done) and generate TLS certs for the local Docker Compose stack
docker-tls:
	mkcert -install
	mkdir -p deploy/docker/certs
	mkcert -cert-file deploy/docker/certs/selfsigned.crt -key-file deploy/docker/certs/selfsigned.key "srdp.localhost" "auth.srdp.localhost" "marimo.srdp.localhost" "dagster.srdp.localhost" "streamlit.srdp.localhost" "marquez.srdp.localhost" "api.srdp.localhost" "duckdb.srdp.localhost"

# Create (or reuse) the local kind cluster
kind-up:
	kind get clusters 2>/dev/null | grep -qx srdp || kind create cluster --config deploy/kubernetes/kind-config.yaml
	kubectl config use-context kind-srdp

# Delete the local kind cluster
kind-down:
	kind delete cluster --name srdp

# Build all images, tagged dev, and load them into kind
kind-load-images: kind-up
	docker build -t {{platform_registry}}/srdp-setup:dev -f deploy/docker/srdp-setup.Dockerfile .
	docker build -t {{platform_registry}}/dagster-webserver:dev -f deploy/docker/dagster-webserver.Dockerfile .
	docker build -t {{platform_registry}}/duckdb-ui:dev -f services/duckdb-ui/Dockerfile .
	docker build -t {{platform_registry}}/hub:dev services/hub
	docker build -t {{registry}}/marimo:dev -f projects/cbs-example/notebooks/Dockerfile .
	docker build -t {{registry}}/srdp-etl:dev -f projects/cbs-example/Dockerfile .
	docker build -t {{registry}}/srdp-api:dev -f projects/cbs-example/api/Dockerfile .
	docker build -t {{registry}}/streamlit:dev -f projects/cbs-example/streamlit/Dockerfile .
	kind load docker-image \
		{{platform_registry}}/srdp-setup:dev \
		{{platform_registry}}/dagster-webserver:dev \
		{{platform_registry}}/duckdb-ui:dev \
		{{platform_registry}}/hub:dev \
		{{registry}}/marimo:dev \
		{{registry}}/srdp-etl:dev \
		{{registry}}/srdp-api:dev \
		{{registry}}/streamlit:dev \
		--name srdp

# Trust mkcert's local CA (no-op once done) and generate TLS certs for the kind stack
local-tls: kind-up
	mkcert -install
	mkdir -p deploy/kubernetes/certs
	mkcert -cert-file deploy/kubernetes/certs/selfsigned.crt -key-file deploy/kubernetes/certs/selfsigned.key "srdp.localhost" "auth.srdp.localhost" "marimo.srdp.localhost" "dagster.srdp.localhost" "streamlit.srdp.localhost" "marquez.srdp.localhost" "api.srdp.localhost" "duckdb.srdp.localhost"
	kubectl create namespace {{namespace}} --dry-run=client -o yaml | kubectl apply -f -
	kubectl create secret tls custom-ingress-cert --namespace {{namespace}} --key deploy/kubernetes/certs/selfsigned.key --cert deploy/kubernetes/certs/selfsigned.crt --dry-run=client -o yaml | kubectl apply -f -

# Fetch the subcharts pinned in Chart.lock into srdp-chart/charts/
chart-deps:
	cd deploy/kubernetes/srdp-chart && \
		awk '$2 == "name:" {name = $3} $1 == "repository:" {print name, $2}' Chart.yaml | \
		while read -r name url; do helm repo add --force-update "srdp-$name" "$url" >/dev/null; done && \
		helm dependency build

# Deploy the full stack to local kind via Helm
local-deploy: kind-load-images chart-deps
	cd deploy/kubernetes && helm upgrade --install srdp srdp-chart --namespace {{namespace}} --create-namespace -f srdp-chart/values.yaml -f srdp-chart/values-local.yaml {{registry_args}} {{local_secrets_args}}
	@echo "Reading Traefik's assigned ClusterIP to wire it into oauth2-proxy's hostAliases..."
	@TRAEFIK_IP=$(kubectl get svc srdp-traefik -n {{namespace}} -o jsonpath='{.spec.clusterIP}'); \
	echo "Traefik ClusterIP: $TRAEFIK_IP"; \
	cd deploy/kubernetes && helm upgrade srdp srdp-chart --namespace {{namespace}} -f srdp-chart/values.yaml -f srdp-chart/values-local.yaml {{registry_args}} {{local_secrets_args}} --set-string "oauth2-proxy.hostAliases[0].ip=$TRAEFIK_IP"

# Uninstall the local Helm release and its PVCs
local-delete:
	helm uninstall srdp -n {{namespace}} || true
	# Dagster run Jobs are created by the run launcher, not by Helm, and their
	# pods keep the ducklake-data PVC in Terminating until they are gone. The
	# srdp-setup hook Job also outlives helm uninstall (no hook-succeeded).
	kubectl delete jobs --all -n {{namespace}} || true
	kubectl delete pvc --all -n {{namespace}} || true

# Start the Docker Compose stack (local dev). Attached by default; pass -d to detach.
# It builds every image from source and tags the platform images dev, whatever SRDP_VERSION says in .env.
docker-up *args:
	cd deploy/docker && SRDP_VERSION=dev docker compose up --build {{args}}

# Start the Docker Compose stack with the published platform images of a release, e.g. `just docker-up-release 0.4.0 -d`.
docker-up-release version *args:
	cd deploy/docker && SRDP_VERSION={{version}} docker compose pull srdp-setup dagster-webserver dagster-daemon duckdb-ui hub
	cd deploy/docker && SRDP_VERSION={{version}} docker compose up {{args}}

# Stop the Docker Compose stack
docker-down:
	cd deploy/docker && docker compose down

# ─── Production / infra ───────────────────────────────────────────────────────

# Provision the Scaleway cluster with OpenTofu
prod-apply:
	cd deploy/opentofu/scaleway && source ./secrets.sh && tofu apply -auto-approve

# Tear down the Scaleway cluster
prod-destroy:
	just prod-uninstall || echo "Helm uninstall skipped (cluster may already be down)"
	cd deploy/opentofu/scaleway && source ./secrets.sh && tofu destroy -auto-approve

# Write the Scaleway kubeconfig locally
prod-use-kubeconfig:
	cd deploy/opentofu/scaleway && tofu output -raw kubeconfig > "{{kubeconfig}}" && echo "kubeconfig written to {{kubeconfig}}"

# Print Traefik's LoadBalancer IP
prod-get-values:
	@echo "Fetching dynamic values..."
	@if [ ! -f "{{kubeconfig}}" ]; then echo "kubeconfig not found, run 'just prod-use-kubeconfig' first"; exit 1; fi
	@KUBECONFIG="{{kubeconfig}}" kubectl get svc srdp-traefik -n {{namespace}} -o jsonpath='{.status.loadBalancer.ingress[0].ip}' | xargs -I{} printf "LOAD_BALANCER_IP:\t%s\n" "{}"

# Deploy only Traefik and the hub page, to get the first LoadBalancer IP
prod-traefik-only:
	cd deploy/kubernetes && \
		if [ ! -f "{{kubeconfig}}" ]; then echo "kubeconfig not found, run 'just prod-use-kubeconfig' first"; exit 1; fi; \
		export KUBECONFIG="{{kubeconfig}}"; \
		helm upgrade --install srdp srdp-chart --namespace {{namespace}} --create-namespace -f srdp-chart/values-prod.yaml {{registry_args}} {{prod_traefik_only_args}}

# Deploy Traefik plus the auth stack only
prod-auth-only:
	cd deploy/kubernetes && \
		if [ ! -f "{{kubeconfig}}" ]; then echo "kubeconfig not found, run 'just prod-use-kubeconfig' first"; exit 1; fi; \
		export KUBECONFIG="{{kubeconfig}}"; \
		helm upgrade srdp srdp-chart --namespace {{namespace}} --reset-values -f srdp-chart/values-prod.yaml {{registry_args}} {{prod_auth_only_args}}

# Deploy the complete production stack
prod-full:
	cd deploy/kubernetes && \
		if [ ! -f "{{kubeconfig}}" ]; then echo "kubeconfig not found, run 'just prod-use-kubeconfig' first"; exit 1; fi; \
		export KUBECONFIG="{{kubeconfig}}"; \
		helm upgrade srdp srdp-chart --namespace {{namespace}} --reset-values -f srdp-chart/values-prod.yaml {{registry_args}}

# Uninstall the production Helm release and release the LoadBalancer
prod-uninstall:
	cd deploy/kubernetes && \
		if [ ! -f "{{kubeconfig}}" ]; then echo "kubeconfig not found, run 'just prod-use-kubeconfig' first"; exit 1; fi; \
		export KUBECONFIG="{{kubeconfig}}"; \
		echo "Deleting LoadBalancer service (releases Scaleway LB)..." && \
		kubectl delete svc srdp-traefik -n {{namespace}} --ignore-not-found && \
		echo "Waiting 30s for LB cleanup..." && sleep 30 && \
		helm uninstall srdp -n {{namespace}} || true && \
		kubectl delete jobs --all -n {{namespace}} --ignore-not-found && \
		kubectl delete pvc --all -n {{namespace}} --ignore-not-found

# ─── Images ───────────────────────────────────────────────────────────────────

# Build and push images to the Scaleway registry
build-and-push:
	source deploy/opentofu/scaleway/secrets.sh && REGISTRY='{{registry}}' bash deploy/opentofu/scaleway/build-and-push.sh

# ─── Development ──────────────────────────────────────────────────────────────

# Install all dependencies and set up pre-commit
init:
	uv sync --all-groups --all-extras
	uv run pre-commit install

# Run all pre-commit hooks on every file
pre-commit:
	uv run pre-commit run --all-files

# Run ruff check and format check
lint:
	uv run ruff check src/ projects/
	uv run ruff format --check src/ projects/

# Run ty on src/srdp
typecheck:
	uv run ty check src/srdp

# Run pytest if tests/ has any, skip with a warning otherwise
test:
	@if [ -d tests ] && find tests -name 'test_*.py' -o -name '*_test.py' 2>/dev/null | grep -q .; then \
		uv run pytest tests --cov=srdp; \
	else \
		echo "warning: no tests/ found, skipping."; \
	fi

# Run lint + typecheck + test
ci: lint typecheck test

# Auto-fix all ruff lint + format issues
fix:
	uv run ruff check --fix src/ projects/
	uv run ruff format src/ projects/

# Cut a release: bump version, run CI, commit, tag
release version:
	./scripts/release.sh "{{version}}"
