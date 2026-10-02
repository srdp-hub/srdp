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

- Release name: `srdp` — services are `srdp-<component>`.
- Namespace: always `srdp`.
- Values layering: `values.yaml` (base) → `values-local.yaml` → `values-prod.yaml` (gitignored).
- Every template guarded by `{{- if .Values.<component>.enabled }}`.
- Domain: `global.domain` is the single source of truth.
- TLS: check `{{ if .Values.traefik.certResolver }}` for ACME vs local cert.
- IngressClass: `srdp-traefik`.

### Container images

Registry: `[deploy] registry` in `srdp.toml`.
The Justfile passes it to builds and to the chart, so never hardcode it elsewhere.

| Image | Source | Tag |
|:---|:---|:---|
| `marimo` | `projects/cbs-example/notebooks/` (build context: repo root) | `v1.0` |
| `quarto` | `services/quarto/` | `v1.0` |
| `srdp-etl` | `projects/cbs-example/` (build context: repo root) | `v1.0` |

Build and push: `just build-and-push`

`srdp-etl` must be built from the repo root (Dockerfile copies `pyproject.toml`, `uv.lock`, `src/`, `projects/`).

### PostgreSQL

Single in-cluster instance (Bitnami, aliased `zitadel-db`) serves the `zitadel`, `dagster`, `marquez` and `ducklake` databases.
No password lives in the values files.
Every consumer reads a Secret with a fixed name: `srdp-postgres`, `srdp-zitadel`, `srdp-oauth2-proxy`, `srdp-dagster-postgresql` and `srdp-marquez` (keys listed in `values.yaml`).
In kind, `templates/local-secrets.yaml` creates them from `localSecrets` in `values-local.yaml`.
Elsewhere, External Secrets creates them.
The Zitadel master key in `srdp-zitadel` must be exactly 32 characters.

If PostgreSQL is redeployed with a stale PVC, delete the PVC and redeploy.

### Known gotchas

- Zitadel master key must be exactly 32 characters.
- `imagePullPolicy: Never` in local — `ImagePullBackOff` means image not in containerd cache.
- `tofu destroy` fails if LB not released first. Run `just prod-uninstall` before destroy.
- Traefik stuck in Init: `ReadWriteOnce` PVC held by previous pod. Delete old pod/PVC.
- OAuth login loops: domain mismatch between `global.domain`, Zitadel OIDC redirect, and oauth2-proxy `--oidc-issuer-url`.
- Dagster CrashLoopBackOff with "password authentication failed": delete `zitadel-db` PVC and redeploy.
