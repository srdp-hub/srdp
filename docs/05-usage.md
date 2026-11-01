---
title: Usage & Verification
icon: lucide/circle-play
---

# Usage & Verification

All apps are protected behind OAuth2-Proxy, so opening one redirects to Zitadel for login first.
You can use the admin credentials from [03-configuration.md](./03-configuration.md).
The hub at `https://srdp.localhost` links to every service.

## Docker Compose

### Access services

Each service has its own `https://<service>.srdp.localhost` address, for example `https://dagster.srdp.localhost`.
The `oauth2-proxy` router rule in `config/traefik/dynamic/srdp.yml` lists every protected host.
The Traefik dashboard at `http://localhost:8080` shows all routers and services, which helps when debugging routing.

### Manage the stack

- Start or rebuild: `just docker-up` (add `-d` to run in the background).
- Stop: `just docker-down`.
- Logs of all services: `cd deploy/docker && docker compose logs -f`, or add a service name such as `marimo`.

> **Warning:** Do not run `docker compose down -v` unless you want to destroy all persistent data, including your Zitadel configuration.

## Kubernetes (local kind cluster)

### Access services

The same addresses as Docker Compose, for example `https://dagster.srdp.localhost`.
Stop the Compose stack first, because both use host ports 80 and 443.
On another cluster, use the domain from `global.domain` in your values.

### Check the release

- `helm list -n srdp`
- `kubectl get pods,svc,ing -n srdp`

### Update or remove the release

- Apply updated values: `just local-deploy`.
- Remove the release and its persistent volumes: `just local-delete`.

### Logs

- All pods: `kubectl logs -n srdp -l app.kubernetes.io/instance=srdp -f`
- One service, for example Marimo: `kubectl logs -n srdp deploy/marimo -f`
- Dagster webserver: `kubectl logs -n srdp deploy/srdp-dagster-webserver -f`
- Dagster daemon: `kubectl logs -n srdp deploy/srdp-dagster-daemon -f`

## Add DuckDB extensions to the DuckDB UI

The DuckDB UI image has no network access at start, so every extension must be in the image.
The image includes `ducklake`, `httpfs`, `postgres` and `ui`.
Use the `DUCKDB_EXTENSIONS` build argument to add more, for example `spatial`.
You do not need to change the Dockerfile.

1. Set the extension names, separated by spaces: `export DUCKDB_EXTENSIONS="spatial excel"`.
2. Build the image. For Docker Compose, run `docker compose build duckdb-ui` in `deploy/docker/`. For kind, run `just kind-load-images`.
3. Run `LOAD spatial;` in the DuckDB UI.

The extensions are fixed when you build the image, and a deployment only pulls the image.
The published `ghcr.io/srdp-hub/duckdb-ui` image has the default extensions only.
For a cluster, build the image with `docker build --build-arg DUCKDB_EXTENSIONS="spatial excel" -f services/duckdb-ui/Dockerfile .`.
Push it to your registry and set the image in your chart values.
`just build-and-push` does not build this image.
A name can contain only lowercase letters, digits and underscores, and the build fails for any other name.
This argument installs core extensions only.
The Python images of a project bake extensions in their own Dockerfile, with `python -m srdp.io.extensions /opt/duckdb/extensions <name> ...`.
