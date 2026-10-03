---
applyTo:
  - "deploy/**/*"
  - "services/**/*"
  - "config/**/*"
  - "Justfile"
---

## Deployment and infrastructure

### Task runner

Always use `just`. Run `just` at the repo root to list all commands.

### Secrets

Never commit secrets. These are gitignored:

- `.env`, `secrets.sh`, `values-prod.yaml`, `kubeconfig.yaml`, `certs/`

Work from example counterparts:

- `deploy/docker/.env.example` → `deploy/docker/.env`
- `deploy/opentofu/scaleway/secrets.sh.example` → secrets.sh
- `deploy/kubernetes/srdp-chart/values-prod.example.yaml` → values-prod.yaml

Placeholders use the `CHANGE_ME_*` prefix convention.

### Helm chart

- Release name: `srdp`, so services are `srdp-<component>`.
- Namespace: always `srdp`.
- Values layering: `values.yaml` (base) → `values-local.yaml` → `values-prod.yaml` (gitignored).
- Every template guarded by `{{- if .Values.<component>.enabled }}`.
- Domain: `global.domain` is the single source of truth.
- TLS: check `{{ if .Values.traefik.certResolver }}` for ACME vs local cert.
- IngressClass: `srdp-traefik`.

### Container images

The images and their Dockerfiles are listed in the `kind-load-images` recipe in the `Justfile`.
Images that are built from the repo root (the Python ones copy `pyproject.toml`, `uv.lock`, `src/` and `projects/`) need the repo root as build context.
Publishing moves from `just build-and-push` (Scaleway registry) to CI publishing to ghcr.io in #82.

### PostgreSQL

One Postgres instance serves the `zitadel`, `dagster`, `marquez` and `ducklake` databases, as a container in Compose and the Bitnami subchart (`zitadel-db`) in Kubernetes.
The `srdp-setup` service (Compose) or Job (Kubernetes) creates every database and role from `srdp.toml` `[setup]` or `values.yaml` `setup.databases`, and resets each role's password to the configured value on every deploy.
Never delete the Postgres volume or PVC to fix a password mismatch, because that destroys all data.
Rerun the deploy instead, as `docs/06-troubleshooting.md` describes.

### Known gotchas

- Zitadel master key must be exactly 32 characters.
- `imagePullPolicy: Never` in local, so `ImagePullBackOff` means the image isn't loaded into kind (`just kind-load-images`).
- `tofu destroy` fails if LB not released first. Run `just prod-uninstall` before destroy.
- Traefik stuck in Init: `ReadWriteOnce` PVC held by previous pod. Delete old pod/PVC.
- OAuth login loops: domain mismatch between `global.domain`, Zitadel OIDC redirect, and oauth2-proxy `--oidc-issuer-url`.
- Dagster CrashLoopBackOff with "password authentication failed": rerun `just local-deploy` (or `helm upgrade`) so `srdp-setup` resets the role password, see `docs/06-troubleshooting.md`.
