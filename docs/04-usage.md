---
title: 4. Usage & Verification
icon: lucide/circle-play
---

# Usage & Verification

All apps are protected behind OAuth2-Proxy, so opening one redirects to Zitadel for login first.
You can use the admin credentials from [02-configuration.md](./02-configuration.md).
The hub at `https://srdp.localhost` links to every service.

## Docker Compose

### Access services

Each service has its own `https://<service>.srdp.localhost` address, for example `https://dagster.srdp.localhost`.
The `oauth2-proxy` router rule in `config/traefik/traefik.yml` lists every protected host.
The Traefik dashboard at `http://localhost:8080` shows all routers and services, which helps when debugging routing.

### Manage the stack

- Start or rebuild: `just docker-up` (add `-d` to run in the background).
- Stop: `just docker-down`.
- Logs of all services: `cd deploy/docker && docker compose logs -f`, or add a service name such as `marimo`.

> **Warning:** Do not run `docker compose down -v` unless you want to destroy all persistent data, including your Zitadel configuration.

## Kubernetes (local kind cluster)

### Access services

The same addresses as Docker Compose, with `:18443` added, for example `https://dagster.srdp.localhost:18443`.
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
