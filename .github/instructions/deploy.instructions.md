---
applyTo:
  - "deploy/**/*"
  - "services/**/*"
  - "config/**/*"
  - "Justfile"
---

## Deployment and infrastructure

### Task runner

Use `just` for every task. Run `just` at the repo root to list the recipes.

### Secrets

Never commit secrets. Git ignores `.env`, `secrets.sh`, `values-prod.yaml`, `kubeconfig.yaml` and `certs/`.

Create each secret file from its example.

| Example | Copy to |
|:---|:---|
| `deploy/docker/.env.example` | `deploy/docker/.env` |
| `deploy/opentofu/scaleway/secrets.sh.example` | `secrets.sh` |
| `deploy/kubernetes/srdp-chart/values-prod.example.yaml` | `values-prod.yaml` |

Start placeholder values with `CHANGE_ME_`.

### Helm chart

- Use the release name `srdp`, so services are named `srdp-<component>`.
- Use the namespace `srdp`.
- Layer the values as `values.yaml`, then `values-local.yaml`, then `values-prod.yaml`.
- Guard every template with `{{- if .Values.<component>.enabled }}`.
- Set the domain only in `global.domain`.
- Check `{{ if .Values.traefik.certResolver }}` to choose between ACME and a local certificate.
- Use the IngressClass `srdp-traefik`.

### Container images

Set the registry in `[deploy] registry` in `srdp.toml`.
The Justfile passes it to builds and to the chart. Do not hardcode it elsewhere.

The `kind-load-images` recipe in the `Justfile` lists every image and its Dockerfile.
Build the Python images with the repo root as context, because they copy `pyproject.toml`, `uv.lock`, `src/` and `projects/`.
`just build-and-push` pushes to the registry.
CI publishes the platform images to ghcr.io (`.github/workflows/images.yml`).

### PostgreSQL

One Postgres instance serves the `zitadel`, `dagster`, `marquez` and `ducklake` databases.
It runs as a container in Compose and as the Bitnami subchart `zitadel-db` in Kubernetes.

`srdp-setup` creates every database and role from `srdp.toml` `[setup]` or `values.yaml` `setup.databases`.
It runs as a service in Compose and as a Job in Kubernetes.
It resets each role password to the configured value on every deploy.

Never delete the Postgres volume or PVC to fix a password mismatch, because that destroys all data.
Rerun the deploy instead, as `docs/06-troubleshooting.md` describes.

Do not put passwords in the chart values files.
Each consumer reads a Secret with a fixed name, and `values.yaml` lists the keys.
The Secrets are `srdp-postgres`, `srdp-zitadel`, `srdp-oauth2-proxy`, `srdp-dagster-postgresql` and `srdp-marquez`.
In kind, `templates/local-secrets.yaml` creates them from `localSecrets` in `values-local.yaml`.
In other clusters, External Secrets creates them.

### Known problems

| Symptom | Cause and fix |
|:---|:---|
| Zitadel does not start | The master key in `srdp-zitadel` must be exactly 32 characters. |
| `ImagePullBackOff` in kind | Local uses `imagePullPolicy: Never`. Run `just kind-load-images`. |
| `tofu destroy` fails | The load balancer still exists. Run `just prod-uninstall` first. |
| Traefik stays in Init | The old pod holds the `ReadWriteOnce` PVC. Delete the old pod or PVC. |
| OAuth login loops | `global.domain`, the Zitadel OIDC redirect and the oauth2-proxy `--oidc-issuer-url` do not match. |
| Dagster `CrashLoopBackOff` with "password authentication failed" | Rerun `just local-deploy` or `helm upgrade`, so `srdp-setup` resets the password. See `docs/06-troubleshooting.md`. |
