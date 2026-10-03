---
title: 2. Local Configuration & Setup
icon: lucide/locate-fixed
---

# Local Configuration & Setup

This guide will walk you through the steps to get the Single-Repo Data Platform (SRDP) running on your local machine.
There are two options:

- **Docker Compose**: simplest, no Kubernetes needed, good for trying out the stack locally.
- **Kubernetes (Helm)**: closer to the production setup, requires a local cluster.

Both options require mkcert for local TLS certificates.
The `*.srdp.localhost` domains resolve to your own machine by themselves, so no hosts-file entries are needed.

---

## Option A: Docker Compose

### 1) Clone the repo

```bash
git clone git@github.com:srdp-hub/srdp.git # or git clone https://github.com/srdp-hub/srdp.git
cd srdp
```

### 2) Install the local CA and generate TLS certificates

The stack serves everything over HTTPS because Zitadel and OAuth2-Proxy require it. [`mkcert`](https://github.com/FiloSottile/mkcert) creates locally-trusted certificates so your browser won't show warnings.

Install `mkcert` following its own instructions, then run:

```bash
just docker-tls   # trusts mkcert's local CA and generates certs in deploy/docker/certs/
```

### 3) Create the environment file

```bash
cp deploy/docker/.env.example deploy/docker/.env
```

Fill in the empty values in `deploy/docker/.env`, which are required, and the comment above each one says how to generate it.
You will need to update `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` after you create the OIDC application in Zitadel; see [First boot: create the Zitadel OIDC application](#first-boot-create-the-zitadel-oidc-application) below.

### 4) Start the stack

```bash
just docker-up
```

This builds the local images and starts every service defined in `deploy/docker/docker-compose.yml`.
The first run takes a few minutes while images are pulled and built.

Quarto is disabled by default, its base image bundles a full Pandoc/TinyTeX/Deno toolchain sized for scientific publishing, heavy for a single static page with no current use. Its source stays at `services/quarto/`, wire it back into `deploy/docker/docker-compose.yml` and `config/traefik/traefik.yml` when it's needed again.

To stop the stack:

```bash
just docker-down
```

> **Warning:** Do not run `docker compose down -v` unless you want to destroy all persistent data, including your Zitadel configuration.

---

## Option B: Kubernetes (Helm)

### 1) Clone the repo

```bash
git clone git@github.com:srdp-hub/srdp.git # or git clone https://github.com/srdp-hub/srdp.git
cd srdp
```

### 2) Reach the cluster

The chart uses `*.srdp.localhost` by default. On the local `kind` cluster this repo is set up for, Traefik's ports are mapped to `127.0.0.1:18080`/`127.0.0.1:18443` (see `deploy/kubernetes/kind-config.yaml`), so `127.0.0.1` is always the right host, but every URL needs the `:18443` (or `:18080` for plain HTTP) suffix. Those aren't 80/443 on purpose: Docker Compose already publishes 80, 443, and 8080 on the host (`deploy/docker/docker-compose.yml`), and both stacks are meant to run side by side without tearing one down to use the other. On any other cluster, point these hostnames at whatever IP you use to reach Traefik: a LoadBalancer's external IP once it's up, or a port-forward's `127.0.0.1` if it isn't reachable directly (see `docs/06-troubleshooting.md`).

### 3) Install the local CA, create the kind cluster, and generate TLS certificates

Install `mkcert` and `kind` following their own instructions, then run:

```bash
just local-tls   # trusts mkcert's local CA, creates the kind cluster (if needed), certs, and the k8s TLS secret
```

`just local-tls` depends on `kind-up`, so it creates the `srdp` kind cluster on first run and points `kubectl` at it; on later runs it just reuses the existing cluster. `kind` runs as plain containers on whatever Docker daemon you already have (Colima, OrbStack, native Docker Engine on Linux, your own preference, this repo doesn't assume one), no separate VM for Kubernetes itself.

### 4) Build local container images

The Helm chart references the application images that the `kind-load-images` recipe builds. `just local-deploy` (step 6) builds and loads them into the kind cluster automatically via the `kind-load-images` recipe, kind nodes don't share the host's image store, so `pullPolicy: Never` needs its own copy loaded with `kind load docker-image`, not just a local `docker build`. Run it standalone if you want to rebuild without a full redeploy:

```bash
just kind-load-images
```

### 5) Fill in secrets and local values

`deploy/kubernetes/srdp-chart/values-local.yaml` holds throwaway development values, so the local cluster runs without extra setup.
Never reuse those values outside your own machine, and keep real secrets out of tracked files (#61).
Keep `custom-ingress-cert` (created above), or point to another TLS secret if you prefer.

### 6) Install the chart locally

```bash
just local-deploy
```

This builds and loads the images (step 4), installs the chart, then reads back Traefik's actual (dynamically-assigned) ClusterIP and feeds it into `oauth2-proxy`'s pod-level host alias in a second pass, since that IP can't be known ahead of the first install. Re-run the same command to pick up updated values.

The chart deploys the full stack: Traefik, PostgreSQL (in-cluster via Bitnami Helm chart), Zitadel, OAuth2-Proxy, Dagster (webserver + daemon + user code), Marimo, the API, DuckDB UI, Marquez, and the hub landing page.
Quarto is disabled by default (`quarto.enabled: false` in `values.yaml`), flip it back on when it's needed again.
PostgreSQL hosts the `zitadel`, `dagster`, `marquez`, and `ducklake` databases.
The Bitnami subchart's `auth.*` fields create `zitadel`, and the `srdp-setup` Job creates the rest from the `setup.databases` list in `values.yaml` on every install and upgrade.

---

## First boot: create the Zitadel OIDC application

OAuth2-Proxy needs an OIDC client registered in Zitadel. Zitadel creates its first instance and admin user automatically on first start, but the OIDC application is created manually through the Zitadel console. Do this once, after the stack is up, for either deployment option.

### 1) Sign in to the Zitadel console

Open `https://auth.srdp.localhost` (Docker Compose) or `https://auth.srdp.localhost:18443` (the local `kind` cluster, see step 2 of Option B) and sign in as the first-instance admin. Zitadel derives the default admin login name from the configured `ExternalDomain`, so for the local stack it is:

- Login name: `zitadel-admin@zitadel.auth.srdp.localhost`
- Password: for the Kubernetes chart, the development default in `values.yaml` under `zitadel.zitadel.configmapConfig.FirstInstance.Org.Human.Password`. The Docker Compose stack requires `ZITADEL_FIRSTINSTANCE_ORG_HUMAN_PASSWORD` to be set in `deploy/docker/.env` (see `.env.example`), Zitadel's own complexity rule applies: uppercase, lowercase, a digit, and a symbol.

If the login name differs, check it under **Users** in the Zitadel console.

### 2) Create the OIDC application

1. Create (or open) a project, then add an application of type **Web**.
2. Use the **Code** authentication flow (client ID + secret).
3. Add a redirect URI `https://<host>/oauth2/callback` for each host that OAuth2-Proxy protects.
   The host list is the `oauth2-proxy` router rule in `config/traefik/traefik.yml`.
   Docker Compose uses no port suffix, and the local `kind` cluster needs `:18443` on every URL (see step 2 of Option B).

Zitadel then shows a **Client ID** and **Client Secret**.

### 3) Apply the credentials

- **Docker Compose**: set `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` in `deploy/docker/.env`, then run `just docker-up` to recreate OAuth2-Proxy with the new values.
- **Kubernetes**: set the `oauth2-proxy` client ID/secret in `values-local.yaml`, then run `just local-deploy`.

**Congratulations! The local environment should now be up and running.** Proceed to the next section, **Usage & Verification**, to confirm that everything is working correctly.
