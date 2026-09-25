---
title: 5. Cloud Deployment
icon: lucide/cloud-cog
---

# Cloud Deployment (Scaleway)

This runbook uses OpenTofu to provision infrastructure and Helm to deploy the chart on Scaleway Kapsule (mutualized). All `just` commands should be run from the **repository root**. Steps that require manual commands specify their working directory explicitly.

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

Run the build script after sourcing credentials (it logs into the Scaleway registry):
```bash
cd deploy/opentofu/scaleway
source ./secrets.sh
./build-and-push.sh
```
This builds and pushes Marimo and srdp-etl (Dagster user code) to `rg.nl-ams.scw.cloud/srdp-registry`. Quarto is disabled by default (`quarto.enabled: false`), see `docs/02-configuration.md`.

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
- Fill in:
  - `global.domain` and `oauth2-proxy` cookie/whitelist domains (use a real domain or `<lb-ip>.nip.io` once you know the load balancer IP).
  - Zitadel master key, admin/user DB passwords, Dagster DB password, and OAuth2 client credentials.
  - ACME email for Traefik (Let's Encrypt).
  - Replace these placeholder values in `values-prod.yaml`:
    - `CHANGE_ME_POSTGRES_PASS`
    - `CHANGE_ME_ZITADEL_DB_PASS`
    - `CHANGE_ME_DAGSTER_DB_PASS`
    - `CHANGE_ME_ZITADEL_MASTERKEY_32CHARS`
    - `CHANGE_ME_ZITADEL_ADMIN_PASS`
    - `CHANGE_ME_OAUTH_COOKIE_SECRET_32`
    - `XXXXXXXXXXXXXXXXXX` and `XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX` for the OAuth2 client ID/secret
- **Keep DB credentials aligned**:
  - `CHANGE_ME_POSTGRES_PASS` must be used consistently for:
    - `zitadel-db.auth.postgresPassword`
    - `zitadel-db.primary.initdb.password`
    - `zitadel.zitadel.secretConfig.Database.Postgres.Admin.Password`
  - `CHANGE_ME_ZITADEL_DB_PASS` must be used consistently for:
    - `zitadel-db.auth.password`
    - `zitadel.zitadel.secretConfig.Database.Postgres.User.Password`
  - `CHANGE_ME_DAGSTER_DB_PASS` must be used consistently for:
    - the Dagster password inside `zitadel-db.primary.initdb.scripts`
    - `dagster.postgresql.postgresqlPassword`
- **Master key format**: ZITADEL expects a 32-character master key string. Generate one, for example, with `tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32`.
- **Password complexity**: Zitadel's first human/admin password must include uppercase, lowercase, digits, and at least one symbol. For example, use `SrdpTest123!` rather than `srdpTest123`.

The production values template enables PostgreSQL replication (`architecture: replication`) with a read replica. Daily backups via a CronJob are already configured in the base `values.yaml`.

## 6) Deploy with Helm (staged rollout)

### A. Bring up Traefik only (to get the LB IP)

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

# 2. Build and push container images (from deploy/opentofu/scaleway/)
cd deploy/opentofu/scaleway && source ./secrets.sh && ./build-and-push.sh

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

All three databases (Zitadel, Dagster, DuckLake catalog) are backed up because they share the same PostgreSQL instance. The `resourcePolicy: keep` ensures backup PVCs survive accidental `helm uninstall`.

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
