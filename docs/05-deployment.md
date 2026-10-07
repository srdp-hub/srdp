---
title: 5. Cloud Deployment
icon: lucide/cloud-cog
---

# Cloud Deployment (Scaleway)

This runbook uses OpenTofu to provision infrastructure and Helm to deploy the chart on Scaleway Kapsule (mutualized). All `just` commands should be run from the **repository root**. Steps that require manual commands specify their working directory explicitly.

If you are new to Kubernetes, read the [Kubernetes primer](09-kubernetes-primer.md) first.
It explains each concept next to its Docker Compose equivalent.

## What OpenTofu provisions

OpenTofu creates the following resources on Scaleway (nl-ams region):
- A VPC and private network
- A Kapsule cluster (mutualized, k8s v1.32) with an autoscaling node pool (PLAY2-MICRO, 1-3 nodes)
- A security group allowing HTTP/HTTPS traffic

PostgreSQL runs **in-cluster** via the Bitnami Helm chart (not as a Scaleway managed database). The container registry (`srdp-registry`) must be created beforehand via the Scaleway Console.

## 1) Prepare cloud credentials

- Copy `deploy/opentofu/scaleway/secrets.sh.example` to `deploy/opentofu/scaleway/secrets.sh` and fill in your Scaleway credentials (SCW_ACCESS_KEY, SCW_SECRET_KEY, SCW_DEFAULT_PROJECT_ID).
- Load them before running OpenTofu:
  ```bash
  cd deploy/opentofu/scaleway
  source ./secrets.sh
  ```

## 2) Build and push container images

Run the build recipe from the repository root. It sources `deploy/opentofu/scaleway/secrets.sh` for the credentials and logs into the registry:
```bash
just build-and-push
```
This builds and pushes Marimo, srdp-etl (Dagster user code), the API and Streamlit to the registry in `srdp.toml` under `[deploy] registry`, tagged with the version in `pyproject.toml`.
The platform images, including `srdp-setup`, come from `ghcr.io/srdp-hub` and need no push.
Quarto is disabled by default (`quarto.enabled: false`).
`docs/02-configuration.md` explains why.

## 3) Provision infrastructure with OpenTofu

```bash
cd deploy/opentofu/scaleway
tofu init -upgrade        # first run only, from deploy/opentofu/scaleway/
cd ../..                  # back to repo root
just prod-apply
```

## 4) Export kubeconfig

```bash
just prod-use-kubeconfig   # from repo root
```

## 5) Prepare production Helm values

- Copy `deploy/kubernetes/srdp-chart/values-prod.example.yaml` to `deploy/kubernetes/srdp-chart/values-prod.yaml` if you are starting fresh.
- Fill in `global.domain`, the `oauth2-proxy` cookie and whitelist domains, and the ACME email for Traefik.
  Use a real domain or `<lb-ip>.nip.io` once you know the load balancer IP.
  Let's Encrypt's HTTP and TLS challenges need the machine to be publicly reachable on that domain.
  A machine that isn't reachable can still use Let's Encrypt through a DNS challenge, if the organisation controls the public DNS of a real domain.
  A machine with only an internal name needs the organisation's own certificate.
- Set the registry in `srdp.toml` under `[deploy] registry`.
  The `prod-*` recipes pass it to the chart as `global.srdpRegistry` and as the `srdp-etl` repository.
- The values files hold no passwords or keys.
  Before you install, create these Secrets in the `srdp` namespace, for example with External Secrets.
  Every consumer reads them by these fixed names.

  | Secret | Keys |
  |:---|:---|
  | `srdp-postgres` | `postgres-password` (superuser), `password` (zitadel user), `replication-password` (replication only) |
  | `srdp-zitadel` | `masterkey`, `config-yaml` |
  | `srdp-oauth2-proxy` | `client-id`, `client-secret`, `cookie-secret` |
  | `srdp-dagster-postgresql` | `postgresql-password` |
  | `srdp-marquez` | `db-password` |

- `config-yaml` in `srdp-zitadel` is a Zitadel config fragment with `Database.Postgres.User.Password`, `Database.Postgres.Admin.Password` and `FirstInstance.Org.Human.Password`.
  The two database passwords must match `password` and `postgres-password` in `srdp-postgres`.
- The `srdp-setup` Job reads `srdp-dagster-postgresql` and `srdp-marquez` too, and applies them to their roles on every install and upgrade.
- **Changing an internal password**: these are service-to-service credentials, so change one only as a deliberate rotation.
  Change the value in its Secret, run `helm upgrade`, then restart the services that use it.
  Marquez restarts by itself. For Dagster, run `kubectl -n srdp rollout restart deploy/srdp-dagster-webserver deploy/srdp-dagster-webserver-read-only deploy/srdp-dagster-daemon deploy/srdp-dagster-user-deployments-srdp-etl`.
- **Master key format**: ZITADEL expects a 32-character master key string. Generate one, for example, with `tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32`.
- **Password complexity**: Zitadel's first human/admin password must include uppercase, lowercase, digits, and at least one symbol. For example, use `SrdpTest123!` rather than `srdpTest123`.

The production values template enables PostgreSQL replication (`architecture: replication`) with a read replica. Daily backups via a CronJob are already configured in the base `values.yaml`.

## 6) Deploy with Helm (staged rollout)

### A. Bring up Traefik and the hub page only (to get the LB IP)

```bash
just prod-traefik-only
```

### B. Update domains once the LB IP exists

```bash
just prod-get-values      # prints LOAD_BALANCER_IP
```
Replace every occurrence of the old LB IP in `values-prod.yaml` with `<LB_IP>.nip.io`. The fields that contain it are:

- `global.domain`
- `zitadel.zitadel.configmapConfig.ExternalDomain`
- `zitadel.zitadel.configmapConfig.firstInstance.org.human.email.address` (the `zitadel-admin@auth.…` address)
- `zitadel.login.customConfigmapConfig`: the `CUSTOM_REQUEST_HEADERS` value (`Host:auth.…` and `X-Zitadel-Public-Host:auth.…`)
- `oauth2-proxy.extraArgs`: `cookie-domain`, `whitelist-domain`, `oidc-issuer-url`, and the `Host:auth.…` header

### C. Enable Zitadel + OAuth2-Proxy

The apps and the `srdp-setup` Job stay off until the final deploy.

```bash
just prod-auth-only
```

### D. Configure Zitadel apps

- In Zitadel (`https://auth.<LB_IP>.nip.io/`), create OIDC apps for Marimo and Dagster with redirect URIs:
  - `https://marimo.<LB_IP>.nip.io/oauth2/callback`
  - `https://dagster.<LB_IP>.nip.io/oauth2/callback`
- Copy the client ID/secret into `values-prod.yaml` (oauth2-proxy config section).

### E. Final deploy with all apps enabled

```bash
just prod-full
```

## Quick reference: full deployment flow

```bash
# 1. Prepare (first time only, from deploy/opentofu/scaleway/)
cd deploy/opentofu/scaleway && source ./secrets.sh && tofu init -upgrade

# 2. Build and push container images (from repo root)
just build-and-push

# 3. Provision infrastructure (from repo root)
just prod-apply
just prod-use-kubeconfig

# 4. Staged Helm rollout (from repo root)
just prod-traefik-only
just prod-get-values                    # note the LOAD_BALANCER_IP
# → Update values-prod.yaml with LB IP in domain fields + secrets
just prod-auth-only
# → Configure Zitadel OIDC apps + copy client ID/secret into values-prod.yaml
just prod-full
```

## Verifying published images

From the first release that includes the images workflow, every release publishes SRDP's platform images (`srdp-setup`, `dagster-webserver`, `duckdb-ui` and `hub`) to `ghcr.io/srdp-hub/<image>:<version>`.
Each image is scanned for critical vulnerabilities before it gets a version tag, signed with cosign through GitHub's OIDC identity, and carries a build provenance attestation and an SBOM.
Each image gets its exact version as a tag, which never moves, and `latest` points at the newest stable release.
Use `latest` for trying SRDP out, and pin the exact version (or the digest) in a deployment, so it only changes when you change it.
The Helm chart pulls these images from `ghcr.io/srdp-hub` at its `appVersion`, which is the release version, and the Dagster webserver and daemon run the same `dagster-webserver` image.
`just docker-up-release <version>` starts Compose with a published release of them, and `just docker-up` builds them from source and tags them `dev`.
The images of your own project still come from your registry, which is set by `[deploy] registry` in `srdp.toml`.

Check an image before you deploy it, with [cosign](https://docs.sigstore.dev/cosign/system_config/installation/) and the [GitHub CLI](https://cli.github.com/):

```bash
# The signature comes from this repo's images workflow
cosign verify ghcr.io/srdp-hub/srdp-setup:<version> \
  --certificate-identity-regexp '^https://github.com/srdp-hub/srdp/.github/workflows/images.yml@' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

# The image was built from this repo, with a traceable commit and workflow run
gh attestation verify oci://ghcr.io/srdp-hub/srdp-setup:<version> --repo srdp-hub/srdp
```


## Secrets management

Secrets are managed through environment-specific mechanisms and are never committed to Git.

| Environment | Mechanism | Example |
|:---|:---|:---|
| Docker Compose | `.env` file (copied from `.env.example`) | Database passwords, OIDC credentials |
| Kubernetes | `values-prod.yaml` (gitignored) | Same, plus Zitadel master key |
| OpenTofu | `secrets.sh` (sourced before `tofu apply`) | Cloud provider API keys |
| CI/CD | GitHub Actions secrets | Registry credentials, kubeconfig |

`.env.example`, `secrets.sh.example`, and `values-prod.example.yaml` document all required variables without values. Pre-commit hooks scan for common secret patterns to catch accidental commits.

## Backup and recovery

PostgreSQL backups are automated via the Bitnami Helm chart's built-in CronJob, configured in `values.yaml`:

```yaml
backup:
  enabled: true
  cronjob:
    schedule: "0 2 * * *"
    storage:
      size: 8Gi
      resourcePolicy: "keep"   # PVC survives helm uninstall
```

All four databases (Zitadel, Dagster, Marquez, DuckLake catalog) are backed up because they share the same PostgreSQL instance. The `resourcePolicy: keep` ensures backup PVCs survive accidental `helm uninstall`.

For DuckLake data files on object storage, enable bucket versioning on your provider to allow file-level recovery.

| Scenario | Recovery |
|:---|:---|
| Accidental table drop | Restore DuckLake catalog from `pg_dump`, re-attach Parquet files |
| PostgreSQL PVC loss | Restore from backup PVC |
| Full cluster loss | Re-provision with OpenTofu, restore PostgreSQL, data files on object storage |

## 7) Clean up

```bash
just prod-destroy
```

This runs `prod-uninstall` first (which deletes the Traefik LoadBalancer service to release the Scaleway-managed LB, uninstalls the Helm release, and only then removes leftover jobs/PVCs) before running `tofu destroy` to remove the cluster and network infrastructure.

If you prefer manual commands:

```bash
# Delete the LB service first (Scaleway LB must be released before the private network can be destroyed)
export KUBECONFIG=deploy/opentofu/scaleway/kubeconfig.yaml
kubectl delete svc srdp-traefik -n srdp --ignore-not-found
sleep 30
helm uninstall srdp -n srdp
kubectl delete jobs --all -n srdp
kubectl delete pvc --all -n srdp
cd deploy/opentofu/scaleway && source ./secrets.sh && tofu destroy -auto-approve
```
