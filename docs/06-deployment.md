---
title: Cloud Deployment
icon: lucide/cloud-cog
---

# Cloud Deployment (Scaleway)

This runbook uses OpenTofu to provision infrastructure and Helm to deploy the chart on Scaleway Kapsule (mutualized). All `just` commands should be run from the **repository root**. Steps that require manual commands specify their working directory explicitly.

If you are new to Kubernetes, read the [Kubernetes primer](10-kubernetes-primer.md) first.
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

## 2) Container images

Every release publishes the platform images and the example project images to `ghcr.io/srdp-hub`, the default registry, so you build and push nothing.
For your own project or your own changes, build the images in your own CI and push them to your registry.
Use the Dockerfiles in the `kind-load-images` recipe of the `Justfile`, and set `[deploy] registry` in `srdp.toml` to your registry.
Quarto is disabled by default (`quarto.enabled: false`) and has no published image.
`docs/03-configuration.md` explains why.

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
  The `prod-*` recipes pass it to the chart as `global.srdpRegistry` and as the `cbs-example` repository.
- The values files hold no passwords or keys.
  Before you install, create these Secrets in the `srdp` namespace, for example with External Secrets.
  Every consumer reads them by these fixed names.
  `deploy/secrets.toml` lists the same Secrets and keys, with the format of each value.

  | Secret | Keys |
  |:---|:---|
  | `srdp-postgres` | `password` (superuser), `replication-password` (replication only) |
  | `srdp-zitadel` | `masterkey`, `db-password` (the `zitadel` database role), `admin-password` (first admin) |
  | `srdp-oauth2-proxy` | `client-id`, `client-secret`, `cookie-secret` |
  | `srdp-dagster` | `postgresql-password` |
  | `srdp-marquez` | `db-password` |
  | `srdp-ducklake-pg-writer` | `password` (the `ducklake_writer` role that Dagster uses) |
  | `srdp-ducklake-pg-reader` | `password` (the read-only `ducklake_reader` role of the four apps) |
  | `srdp-ducklake-s3-writer` | `key-id`, `secret` (only with `ducklakeStorage.backend: s3`) |
  | `srdp-ducklake-s3-reader` | `key-id`, `secret` (only with `ducklakeStorage.backend: s3`) |

- The DuckLake catalog has two Postgres roles.
  Dagster writes the catalog as `ducklake_writer`, which owns the `ducklake` database.
  The four apps connect as `ducklake_reader`, which can only read the catalog tables.
  No app gets the writer Secret or the Postgres superuser Secret.
- `values-prod.example.yaml` keeps DuckLake on S3 with no `ducklake-data` volume, so a deploy from it needs both S3 Secrets.
- With `ducklakeStorage.backend: s3`, Dagster and its run pods get the writer key and the four apps get the read-only key.
  Give the reader key read access to the lake prefix only, because a SQL console like duckdb-ui runs with the full authority of its key.

- Zitadel never gets the Postgres superuser password.
  The `srdp-setup` Job creates the `zitadel` role from `db-password` in `srdp-zitadel`, and Zitadel logs in with that same value.
- The `srdp-setup` Job reads `srdp-zitadel`, `srdp-dagster`, `srdp-marquez` and the two `srdp-ducklake-pg-*` Secrets, and applies them to their roles on every install and upgrade.
  The Job also gives `ducklake_reader` its read rights, makes `ducklake_writer` the owner of an older catalog, and creates the catalog tables once.
  Set `setup.manageDatabases: false` when someone else creates the databases and roles.
  The Job then only checks that it can connect and that the databases exist, and it creates the catalog tables as `ducklake_writer`.
  In that case create the roles yourself.
  Make `ducklake_writer` the owner of the `ducklake` database, and grant `ducklake_reader` `USAGE` on the `public` schema and `SELECT` on its tables.
  Run `REVOKE CREATE ON SCHEMA public FROM PUBLIC`, because before Postgres 15 every role can create tables there.
  Add `ALTER DEFAULT PRIVILEGES FOR ROLE ducklake_writer IN SCHEMA public GRANT SELECT ON TABLES TO ducklake_reader`, because DuckLake creates tables at run time.
- **Changing an internal password**: these are service-to-service credentials, so change one only as a deliberate rotation.
  Change the value in its Secret, run `helm upgrade`, then restart the services that use it.
  Marquez restarts by itself. For Dagster, run `kubectl -n srdp rollout restart deploy/srdp-dagster-webserver deploy/srdp-dagster-webserver-read-only deploy/srdp-dagster-daemon deploy/srdp-dagster-user-deployments-cbs-example`.
  For Zitadel, run `kubectl -n srdp rollout restart deploy/srdp-zitadel`, because a running pod keeps the old password.
  The new pod waits in an init container until its new password works.
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

The apps stay off until the final deploy.
The `srdp-setup` Job runs from this step on, because it creates Zitadel's database, so every Secret from section 5 must exist before you run it.

```bash
just prod-auth-only
```

### D. Configure the Zitadel app

Production has no recipe for this step yet.
#60 moves it into `srdp-setup`.

1. In the Zitadel console (`https://auth.<LB_IP>.nip.io/`), create a project and an application of type **Web** with the **Code** flow.
2. In the token settings of the application, set the auth token type to **JWT**.
   OAuth2-Proxy accepts a user's token as a `Bearer` token only when it is a JWT.
3. Add a redirect URI `https://<host>/oauth2/callback` for each app host that `values-prod.yaml` enables.
   The hosts are `<domain>` for the hub, and `api`, `dagster`, `duckdb`, `marimo`, `marquez`, `streamlit` and `quarto` in front of `<domain>`.
   In this step the apps are still off, so `kubectl get ingress auth-routes-ingress -n srdp` lists the hub only.
   After step E, it lists every host, so compare it with the redirect URIs.
4. Put the client ID and the secret in the `srdp-oauth2-proxy` Secret, as the keys `client-id` and `client-secret`.
5. Restart OAuth2-Proxy with `kubectl rollout restart deployment/srdp-oauth2-proxy -n srdp`.

### E. Final deploy with all apps enabled

```bash
just prod-full
```

## Quick reference: full deployment flow

```bash
# 1. Prepare (first time only, from deploy/opentofu/scaleway/)
cd deploy/opentofu/scaleway && source ./secrets.sh && tofu init -upgrade

# 2. Provision infrastructure (from repo root)
just prod-apply
just prod-use-kubeconfig

# 3. Staged Helm rollout (from repo root)
just prod-traefik-only
just prod-get-values                    # note the LOAD_BALANCER_IP
# → Update values-prod.yaml with LB IP in domain fields + secrets
just prod-auth-only
# → Create the Zitadel app and store its client in the srdp-oauth2-proxy Secret (step D)
just prod-full
```

## Verifying published images

From the first release that includes the images workflow, every release publishes SRDP's platform images (`srdp-setup`, `dagster-webserver`, `duckdb-ui` and `hub`) to `ghcr.io/srdp-hub/<image>:<version>`.
From v0.4.0, it also publishes the example project images (`cbs-example`, `srdp-api`, `marimo` and `streamlit`) there, so the chart's defaults work without your own registry.
Each image is scanned for critical vulnerabilities before it gets a version tag, signed with cosign through GitHub's OIDC identity, and carries a build provenance attestation and an SBOM.
Each image gets its exact version as a tag, which never moves, and `latest` points at the newest stable release.
Use `latest` for trying SRDP out, and pin the exact version (or the digest) in a deployment, so it only changes when you change it.
The Helm chart pulls these images from `ghcr.io/srdp-hub` at its `appVersion`, which is the release version, and the Dagster webserver and daemon run the same `dagster-webserver` image.
`just docker-up-release <version>` starts Compose with the published images of that release, the example project images included, and `just docker-up` builds them all from source and tags them `dev`.
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
