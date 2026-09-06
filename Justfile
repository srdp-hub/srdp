set shell := ["bash", "-c"]
set dotenv-load := false

namespace := "srdp"
kubeconfig := justfile_directory() + "/deploy/opentofu/scaleway/kubeconfig.yaml"

default: help

help:
	@just --list

# ─── Scaleway landing zone (deploy/scaleway) ─────────────────────────────────

# Run a Scaleway-blueprint recipe, e.g. `just scaleway doctor`, `just scaleway bootstrap-all`,
# `just scaleway up dev`. Recipes + config: deploy/scaleway/{Justfile,.env}.
scaleway *args:
	@just -f deploy/scaleway/Justfile -d deploy/scaleway {{args}}

# ─── Local development ────────────────────────────────────────────────────────

# Generate mkcert TLS certs for the local Docker Compose stack
docker-tls:
	mkdir -p deploy/docker/certs
	mkcert -cert-file deploy/docker/certs/selfsigned.crt -key-file deploy/docker/certs/selfsigned.key "srdp.localhost" "auth.srdp.localhost" "marimo.srdp.localhost" "quarto.srdp.localhost" "dagster.srdp.localhost" "streamlit.srdp.localhost" "marquez.srdp.localhost" "api.srdp.localhost" "duckdb.srdp.localhost"

# Generate mkcert TLS certs and create the k8s TLS secret
local-tls:
	mkdir -p deploy/kubernetes/certs
	mkcert -cert-file deploy/kubernetes/certs/selfsigned.crt -key-file deploy/kubernetes/certs/selfsigned.key "srdp.localhost" "auth.srdp.localhost" "marimo.srdp.localhost" "quarto.srdp.localhost" "dagster.srdp.localhost" "streamlit.srdp.localhost" "marquez.srdp.localhost" "api.srdp.localhost" "duckdb.srdp.localhost"
	kubectl create namespace {{namespace}} --dry-run=client -o yaml | kubectl apply -f -
	kubectl create secret tls custom-ingress-cert --namespace {{namespace}} --key deploy/kubernetes/certs/selfsigned.key --cert deploy/kubernetes/certs/selfsigned.crt --dry-run=client -o yaml | kubectl apply -f -

# Deploy the full platform to the local k8s cluster
local-deploy:
	cd deploy/kubernetes/srdp-chart && helm dependency update
	cd deploy/kubernetes && helm upgrade --install srdp srdp-chart --namespace {{namespace}} --create-namespace -f srdp-chart/values.yaml -f srdp-chart/values-local.yaml

# Tear down local k8s deployment and delete PVCs
local-delete:
	helm uninstall srdp -n {{namespace}} || true
	kubectl delete pvc --all -n {{namespace}} || true

# Start the Docker Compose stack (local dev). Attached by default; pass -d to detach.
docker-up *args:
	cd deploy/docker && docker compose up --build {{args}}

# Stop the Docker Compose stack
docker-down:
	cd deploy/docker && docker compose down

# ─── Production / infra ───────────────────────────────────────────────────────

prod-apply:
	cd deploy/opentofu/scaleway && source ./secrets.sh && tofu apply -auto-approve

prod-destroy:
	just prod-uninstall || echo "Helm uninstall skipped (cluster may already be down)"
	cd deploy/opentofu/scaleway && source ./secrets.sh && tofu destroy -auto-approve

prod-use-kubeconfig:
	cd deploy/opentofu/scaleway && tofu output -raw kubeconfig > "{{kubeconfig}}" && echo "kubeconfig written to {{kubeconfig}}"

prod-get-values:
	@echo "Fetching dynamic values..."
	@if [ ! -f "{{kubeconfig}}" ]; then echo "kubeconfig not found, run 'just prod-use-kubeconfig' first"; exit 1; fi
	@KUBECONFIG="{{kubeconfig}}" kubectl get svc srdp-traefik -n {{namespace}} -o jsonpath='{.status.loadBalancer.ingress[0].ip}' | xargs -I{} printf "LOAD_BALANCER_IP:\t%s\n" "{}"

prod-traefik-only:
	cd deploy/kubernetes && \
		if [ ! -f "{{kubeconfig}}" ]; then echo "kubeconfig not found, run 'just prod-use-kubeconfig' first"; exit 1; fi; \
		export KUBECONFIG="{{kubeconfig}}"; \
		helm upgrade --install srdp srdp-chart --namespace {{namespace}} --create-namespace -f srdp-chart/values-prod.yaml --set zitadel.enabled=false --set oauth2-proxy.enabled=false --set dagster.enabled=false --set marimo.enabled=false --set quarto.enabled=false

prod-auth-only:
	cd deploy/kubernetes && \
		if [ ! -f "{{kubeconfig}}" ]; then echo "kubeconfig not found, run 'just prod-use-kubeconfig' first"; exit 1; fi; \
		export KUBECONFIG="{{kubeconfig}}"; \
		helm upgrade srdp srdp-chart --namespace {{namespace}} --reset-values -f srdp-chart/values-prod.yaml --set zitadel.enabled=true --set oauth2-proxy.enabled=true --set dagster.enabled=false --set marimo.enabled=false --set quarto.enabled=false

prod-full:
	cd deploy/kubernetes && \
		if [ ! -f "{{kubeconfig}}" ]; then echo "kubeconfig not found, run 'just prod-use-kubeconfig' first"; exit 1; fi; \
		export KUBECONFIG="{{kubeconfig}}"; \
		helm upgrade srdp srdp-chart --namespace {{namespace}} --reset-values -f srdp-chart/values-prod.yaml

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

# Build and push all service images to the Scaleway registry
build-and-push:
	source deploy/opentofu/scaleway/secrets.sh && bash deploy/opentofu/scaleway/build-and-push.sh

# ─── Development ──────────────────────────────────────────────────────────────

# Install all deps including dev groups and set up pre-commit
init:
	uv sync --all-groups --all-extras
	uv run pre-commit install

# Run ruff linter + formatter check across the whole repo
lint:
	uv run ruff check src/ projects/
	uv run ruff format --check src/ projects/

# Run ty type checker on srdp
typecheck:
	uv run ty check src/srdp

# Run the test suite with coverage
test:
	uv run pytest tests --cov=srdp

# Run lint + typecheck + test
ci: lint typecheck test

# Auto-fix all ruff lint + format issues
fix:
	uv run ruff check --fix src/ projects/
	uv run ruff format src/ projects/
