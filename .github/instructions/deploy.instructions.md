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

Registry: `[deploy] registry` in `srdp.toml`.
The Justfile passes it to builds and to the chart, so never hardcode it elsewhere.

The images and their Dockerfiles are listed in the `kind-load-images` recipe in the `Justfile`.
Images that are built from the repo root (the Python ones copy `pyproject.toml`, `uv.lock`, `src/` and `projects/`) need the repo root as build context.
`just build-and-push` pushes to that registry, and CI publishes the platform images to ghcr.io (`.github/workflows/images.yml`).

### PostgreSQL

One Postgres instance serves the `zitadel`, `dagster`, `marquez` and `ducklake` databases, as a container in Compose and the Bitnami subchart (`zitadel-db`) in Kubernetes.
The `srdp-setup` service (Compose) or Job (Kubernetes) creates every database and role from `srdp.toml` `[setup]` or `values.yaml` `setup.databases`, and resets each role's password to the configured value on every deploy.
Never delete the Postgres volume or PVC to fix a password mismatch, because that destroys all data.
Rerun the deploy instead, as `docs/06-troubleshooting.md` describes.

No password lives in the chart's values files.
Every consumer reads a Secret with a fixed name: `srdp-postgres`, `srdp-zitadel`, `srdp-oauth2-proxy`, `srdp-dagster-postgresql` and `srdp-marquez` (keys listed in `values.yaml`).
DuckLake on S3 (`ducklakeStorage.backend: s3`) adds `srdp-ducklake-s3-writer` for Dagster and `srdp-ducklake-s3-reader` for the apps.
Never give an app the writer Secret, because a SQL console runs with the full authority of its S3 key.
In kind, `templates/local-secrets.yaml` creates them from `localSecrets` in `values-local.yaml`.
Elsewhere, External Secrets creates them.
The Zitadel master key in `srdp-zitadel` must be exactly 32 characters.

### Known gotchas

- Zitadel master key must be exactly 32 characters.
- `imagePullPolicy: Never` in local, so `ImagePullBackOff` means the image isn't loaded into kind (`just kind-load-images`).
- `tofu destroy` fails if LB not released first. Run `just prod-uninstall` before destroy.
- Traefik stuck in Init: `ReadWriteOnce` PVC held by previous pod. Delete old pod/PVC.
- OAuth login loops: domain mismatch between `global.domain`, Zitadel OIDC redirect, and oauth2-proxy `--oidc-issuer-url`.
- Dagster CrashLoopBackOff with "password authentication failed": rerun `just local-deploy` (or `helm upgrade`) so `srdp-setup` resets the role password, see `docs/06-troubleshooting.md`.
