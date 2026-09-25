---
title: 6. Troubleshooting
icon: lucide/life-buoy
---

# Troubleshooting

### Ingress returns 404 or invalid cert

- Make sure the TLS secret `custom-ingress-cert` exists in the `srdp` namespace (`kubectl get secret custom-ingress-cert -n srdp`).
- For production, Traefik uses Let's Encrypt via the ACME TLS challenge; verify the `certResolver` and ACME email are set in `values-prod.yaml`.
- Regenerate with `mkcert` if the hostnames or IP changed (local dev).
- Confirm your hosts file points `auth/marimo/dagster.<domain>` to the Traefik IP.

### `kind create cluster` fails with "port is already allocated"

- `deploy/kubernetes/kind-config.yaml` deliberately maps host ports 18080/18443, not 80/443/8080, because `deploy/docker/docker-compose.yml` already publishes exactly those three (http, https, the Traefik dashboard), and both stacks are meant to run side by side. If you still hit this error, something else on your machine is already using 18080/18443, check `lsof -i :18443` (or the equivalent) and either free it or pick different ports in `kind-config.yaml`, matching them up with `values.yaml`'s `traefik.ports.*.nodePort`.

### Pods stuck in `Pending`

- Check storage and DB connectivity: `kubectl describe pod <name> -n srdp`.
- PostgreSQL runs in-cluster; verify the service is up and its PVC is bound:
  - Local (standalone): service `db-postgresql`, PVC `data-db-postgresql-0`
  - Production (replication): service `db-postgresql-primary`, PVC `data-db-postgresql-primary-0`

### LoadBalancer stays in `Pending`

- Traefik needs a LoadBalancer-capable environment. Verify your Scaleway account quotas and that the service type is `LoadBalancer` (see `values-prod.yaml`).

### OAuth login loops or 401s

- Ensure `global.domain`, `zitadel.zitadel.configmapConfig.ExternalDomain`, and the `oauth2-proxy.extraArgs` cookie/whitelist domains all match the URL you are using.
- Re-check the Zitadel client ID/secret and redirect URIs.

### Browser rejects the self-signed cert

- Import the `mkcert` root CA (printed during `mkcert -install`) or trust `kubernetes/certs/selfsigned.crt` locally while developing.

### ACME errors / rate limits

- Let's Encrypt blocks `nip.io` frequently and requires public reachability on ports 80/443. Open those ports on the load balancer/security group, or temporarily point Traefik to the staging CA until production issuance succeeds.

### Zitadel login-client missing

- If the Postgres DB already contains Zitadel data, the `login-client` PAT will not be recreated. Use a fresh database (or drop the existing schema) before re-running the chart.

### Dagster webserver CrashLoopBackOff with `password authentication failed for user "dagster"`

- Cause: `zitadel-db.primary.initdb.scripts` only run on first PostgreSQL initialization. If you reused an old PVC, the `dagster` role/database may be missing.
- Quick fix (keeps existing data), adjust the pod name and password for your environment:
  - Local: `kubectl -n srdp exec -i db-postgresql-0 -- bash -lc "export PGPASSWORD='<your-postgres-password>'; psql -h 127.0.0.1 -U postgres -d postgres"`
  - Production: `kubectl -n srdp exec -i db-postgresql-primary-0 -- bash -lc "export PGPASSWORD='<your-postgres-password>'; psql -h 127.0.0.1 -U postgres -d postgres"`
  - Then run:
    - `CREATE ROLE dagster LOGIN PASSWORD '<your-dagster-password>';` (or `ALTER ROLE ...` if it exists)
    - `CREATE DATABASE dagster OWNER dagster;` (if missing)
    - `GRANT ALL PRIVILEGES ON DATABASE dagster TO dagster;`
  - `kubectl -n srdp rollout restart deploy/srdp-dagster-webserver deploy/srdp-dagster-daemon`
- Clean reset option (local dev): `just local-delete` to remove PVCs, then `just local-deploy` to let init scripts recreate databases from scratch.

### Updated container image not picked up after rebuild

- Cause: The default `imagePullPolicy` is `IfNotPresent`. If you rebuild an image with the same tag (e.g. `v1.0`), Kubernetes will keep using the cached version.
- Fix: Bump the image tag (e.g. `v1.0` → `v1.1`) in both the build command and `values-prod.yaml`, then redeploy. Alternatively, set `imagePullPolicy: Always` in your values file, but this is slower for routine deployments.

### Orphaned Scaleway Load Balancer blocks `tofu destroy`

- Symptom: `tofu destroy` fails with `Private Network must be empty to be deleted`.
- Cause: Kapsule creates a Scaleway-managed Load Balancer when a `type: LoadBalancer` service (Traefik) is deployed via Helm. This LB is not tracked by OpenTofu, so it remains attached to the private network after the cluster is deleted.
- Fix: The updated `just prod-destroy` handles this automatically. If you already hit this error, delete the LB via the Scaleway API:
  ```bash
  source ./secrets.sh
  curl -s -H "X-Auth-Token: $SCW_SECRET_KEY" "https://api.scaleway.com/lb/v1/zones/nl-ams-1/lbs" | python3 -m json.tool
  curl -X DELETE -H "X-Auth-Token: $SCW_SECRET_KEY" "https://api.scaleway.com/lb/v1/zones/nl-ams-1/lbs/<LB_ID>?release_ip=true"
  ```
  Wait ~30 seconds, then retry `tofu destroy`.

### Traefik stuck in Init

- The Traefik PVC is ReadWriteOnce; if an old pod still holds it, new pods stay in `Init` with a multi-attach warning. Delete the old Traefik pod (or the PVC if needed) so the new pod can mount `/data`.

### Zitadel project and apps missing after a restart (Docker Compose)

- Cause: you ran `docker compose down -v`, which removes all persistent volumes.
- Fix: use `docker compose down` (without `-v`) to stop the stack. Only use `-v` when you intend to reset all data.
