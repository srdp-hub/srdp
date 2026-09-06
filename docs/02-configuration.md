---
title: 2. Local Configuration & Setup
icon: lucide/locate-fixed
---

# Local Configuration & Setup

This guide will walk you through the steps to get the Single Repo Data Platform (SRDP) running on your local machine. There are two options:

- **Docker Compose** — simplest, no Kubernetes needed, good for trying out the stack locally.
- **Kubernetes (Helm)** — closer to the production setup, requires a local cluster.

Both options require mkcert for local TLS certificates and `/etc/hosts` entries for the `*.srdp.localhost` domains.

---

## Option A: Docker Compose

### 1) Clone the repo

```bash
git clone git@github.com:srdp-hub/srdp.git # or git clone https://github.com/srdp-hub/srdp.git
cd srdp
```

### 2) Point DNS at localhost

Add the following line to your hosts file (`/etc/hosts` on macOS/Linux):

```
127.0.0.1 auth.srdp.localhost marimo.srdp.localhost quarto.srdp.localhost dagster.srdp.localhost
```

### 3) Install the local CA and generate TLS certificates

The stack serves everything over HTTPS because Zitadel and OAuth2-Proxy require it. [`mkcert`](https://github.com/FiloSottile/mkcert) creates locally-trusted certificates so your browser won't show warnings.

```bash
brew install mkcert   # or see mkcert docs for other platforms
mkcert -install       # one-time: installs a local Certificate Authority
just docker-tls       # generates certs in deploy/docker/certs/
```

### 4) Create the environment file

```bash
cp deploy/docker/.env.example deploy/docker/.env
```

The defaults in `.env.example` are fine for local development. You will need to update `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` after you create the OIDC application in Zitadel; see [First boot: create the Zitadel OIDC application](#first-boot-create-the-zitadel-oidc-application) below.

### 5) Start the stack

```bash
just docker-up
```

This builds the Marimo and Quarto images locally and starts all services: Traefik, PostgreSQL, Zitadel, OAuth2-Proxy, Dagster (webserver, daemon, and user code), Marimo, and Quarto. First run will take a few minutes while images are pulled and built.

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

### 2) Point DNS at your cluster

The chart uses `*.srdp.localhost` by default. Point those hostnames at the IP you will use to reach Traefik:

- For NodePort/local clusters: `127.0.0.1` is usually fine.
- For a LoadBalancer: use the external IP once Traefik comes up.

Add one line to your hosts file (`/etc/hosts` on macOS/Linux):

```
127.0.0.1 auth.srdp.localhost marimo.srdp.localhost quarto.srdp.localhost dagster.srdp.localhost
```

### 3) Install the local CA and generate TLS certificates

```bash
brew install mkcert   # or see mkcert docs for other platforms
mkcert -install       # one-time: installs a local Certificate Authority
just local-tls        # generates certs and creates the k8s TLS secret
```

Or manually:

```bash
mkdir -p deploy/kubernetes/certs
mkcert -cert-file deploy/kubernetes/certs/selfsigned.crt -key-file deploy/kubernetes/certs/selfsigned.key \
  "auth.srdp.localhost" "marimo.srdp.localhost" "quarto.srdp.localhost" "dagster.srdp.localhost"

kubectl create namespace srdp --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret tls custom-ingress-cert \
  --namespace srdp \
  --key deploy/kubernetes/certs/selfsigned.key \
  --cert deploy/kubernetes/certs/selfsigned.crt \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 4) Build local container images

The Helm chart references three application images. For local development the pull policy is set to `Never`, so the images must exist in your local Docker/containerd cache:

```bash
docker build -t rg.nl-ams.scw.cloud/srdp-registry/marimo:v1.0 -f projects/cbs-example/notebooks/Dockerfile .
docker build -t rg.nl-ams.scw.cloud/srdp-registry/quarto:v1.0 services/quarto
docker build -t rg.nl-ams.scw.cloud/srdp-registry/srdp-etl:v1.0 -f projects/cbs-example/Dockerfile .
```

### 5) Fill in secrets and local values

Update `deploy/kubernetes/srdp-chart/values-local.yaml` before installing:

- set your own Zitadel master key, DB passwords, OAuth2 client values, and cookie secret
- keep `custom-ingress-cert` (created above) or point to another TLS secret if you prefer.

### 6) Install the chart locally

```bash
cd deploy/kubernetes/srdp-chart
helm dependency update
helm upgrade --install srdp . \
  --namespace srdp --create-namespace \
  -f values.yaml \
  -f values-local.yaml
```

Or simply run:
```bash
just local-deploy
```

To re-run with updated values, run the same `helm upgrade` command (or `just local-deploy`).

The chart deploys the full stack: Traefik, PostgreSQL (in-cluster via Bitnami Helm chart), Zitadel, OAuth2-Proxy, Dagster (webserver + daemon + user code), Marimo, and Quarto. PostgreSQL hosts the `zitadel` and `dagster` databases, created automatically via `zitadel-db.primary.initdb.scripts`; the `ducklake` catalog database is created later by the DuckLake IO manager on first use.

---

## First boot: create the Zitadel OIDC application

OAuth2-Proxy needs an OIDC client registered in Zitadel. Zitadel creates its first instance and admin user automatically on first start, but the OIDC application is created manually through the Zitadel console. Do this once, after the stack is up, for either deployment option.

### 1) Sign in to the Zitadel console

Open `https://auth.srdp.localhost` and sign in as the first-instance admin. Zitadel derives the default admin login name from the configured `ExternalDomain`, so for the local stack it is:

- Login name: `zitadel-admin@zitadel.auth.srdp.localhost`
- Password: `srdpTest123!` for the Kubernetes chart (set in `values.yaml`). The Docker Compose stack requires `ZITADEL_FIRSTINSTANCE_ORG_HUMAN_PASSWORD` to be set in `deploy/docker/.env` (see `.env.example`), Zitadel's own complexity rule applies: uppercase, lowercase, a digit, and a symbol.

If the login name differs, check it under **Users** in the Zitadel console.

### 2) Create the OIDC application

1. Create (or open) a project, then add an application of type **Web**.
2. Use the **Code** authentication flow (client ID + secret).
3. Add a redirect URI for each protected service:
   - `https://marimo.srdp.localhost/oauth2/callback`
   - `https://quarto.srdp.localhost/oauth2/callback`
   - `https://dagster.srdp.localhost/oauth2/callback`
   - `https://streamlit.srdp.localhost/oauth2/callback`

Zitadel then shows a **Client ID** and **Client Secret**.

### 3) Apply the credentials

- **Docker Compose**: set `OIDC_CLIENT_ID` and `OIDC_CLIENT_SECRET` in `deploy/docker/.env`, then run `just docker-up` to recreate OAuth2-Proxy with the new values.
- **Kubernetes**: set the `oauth2-proxy` client ID/secret in `values-local.yaml`, then run `just local-deploy`.

**Congratulations! The local environment should now be up and running.** Proceed to the next section, **Usage & Verification**, to confirm that everything is working correctly.
