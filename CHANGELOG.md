# Changelog

All notable changes to SRDP are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Signed platform images: every release builds `srdp-setup`, `dagster-webserver`, `duckdb-ui` and `hub` for amd64 and arm64, scans them for critical vulnerabilities, signs them with cosign, attests their provenance and publishes them to `ghcr.io/srdp-hub` once all four pass, tagged with the exact version (which never moves) and `latest` for the newest stable release. Pull requests and pushes to `main` that touch the images build and scan them without publishing. See "Verifying published images" in the deployment docs.
- Dependabot for GitHub Actions and the platform images' base images, which are pinned by digest.
- `just pre-commit`, which runs every pre-commit hook on all files.
- `srdp-setup` service that creates every service database and role before the services that need them start, on Docker Compose and Kubernetes.
  It runs on every deploy, so it also repairs an existing volume that is missing a database, and it resets each role's password to the configured value.
  The database list lives in the `[setup]` table of the new repo-root `srdp.toml` (Compose) and in `setup.databases` in the chart's `values.yaml` (Kubernetes).
- `srdp.toml`, the start of the central platform config from #42.
  It holds no secrets, and `[setup]` is its first table.
- `MARQUEZ_DB_PASSWORD` in `deploy/docker/.env`.
  Existing Compose setups need to add it to `.env`.
- Streamlit in the Helm chart, behind the login on `streamlit.<domain>`.
- A shared `ducklake-data` volume in the chart, so the apps read the Parquet files that Dagster run pods write.
- `global.srdpRegistry` and `global.imagePullSecrets` in the chart.
  `srdp.toml` holds the registry under `[deploy] registry`, and every `Justfile` deploy and build recipe reads it from there.
- `S3StorageBackend` stores the DuckLake data files in S3-compatible object storage (Scaleway, Hetzner, Garage) when `DUCKLAKE_STORAGE_BACKEND=s3` is set. Its settings live in their own `S3StorageSettings` (`DUCKLAKE_S3_*`), apart from the Postgres catalog settings. Endpoint, URL style and region are required, with no AWS defaults, and the DuckDB secret is scoped to the lake prefix. The default stays `local`, so nothing changes without the setting. An empty `DUCKLAKE_*` variable counts as unset. A catalog keeps the data path it was created with, so switching an existing catalog between `local` and `s3` needs a new catalog database. Part of #56.
- `srdp.io.dbt_plugin`, a dbt-duckdb plugin that attaches DuckLake with the same storage settings as Dagster, so a dbt profile no longer needs its own copy of them.
- ADR-0012, on how SRDP uses dlt with Dagster and DuckLake, with ADR-0002, ADR-0003 and ADR-0004 revised to secure by default.
- `domain` in the `[deploy]` table of `srdp.toml`, the base domain of every hostname (#91).
  The `Justfile` passes it to Docker Compose as `SRDP_DOMAIN`, to the certificates of `just docker-tls` and `just local-tls`, and to `just local-deploy`.
  The production Helm recipes keep reading the domain from `values-prod.yaml`.
- `ACME_CA_SERVER` in `deploy/docker/.env`, optional, to point Let's Encrypt at its staging directory while trying out a new domain.

### Security

- A DuckLake settings error no longer prints the values it was given, so a misconfigured start cannot write the Postgres password or the S3 secret to the logs.

### Changed

- The chart holds no passwords or keys.
  Every consumer reads a fixed-name Secret (`srdp-postgres`, `srdp-zitadel`, `srdp-oauth2-proxy`, `srdp-dagster-postgresql`, `srdp-marquez`).
  In kind, `templates/local-secrets.yaml` creates them from `values-local.yaml`.
  Existing `values-prod.yaml` files must drop their password values and create these Secrets instead.
  In kind, every pod that reads one of them restarts when `values-local.yaml` changes, through a `checksum/local-secrets` pod annotation.
- Image `repository` values of the chart's own apps are bare names such as `marimo`, prefixed by `global.srdpRegistry`.
  Existing `values-prod.yaml` files that set a full repository path must shorten it.
- Dagster queues runs and launches at most 3 at a time, of which at most one backfill (`workload_kind: backfill`), on both Compose and Kubernetes.
- The base and fast-lane Kubernetes run profiles request 512Mi with a 1536Mi limit, since a full `srdp_etl_job` run peaks at about 1Gi.
- `just prod-traefik-only` deploys only Traefik and the hub page, and `just prod-auth-only` adds only Zitadel, its database and OAuth2-Proxy.
  Both leave every app and the `srdp-setup` Job off.
- `srdp-setup` and `duckdb-ui` build in the uv image and run on `python:3.12-slim-bookworm`, without uv at runtime, and the hub image applies Alpine security updates, so all platform images pass the critical vulnerability scan.
- `just docker-tls` and `just local-tls` install mkcert's local CA themselves, so setup is install mkcert and run the recipe.
- The setup docs link to each tool's own install instructions, drop the unneeded `/etc/hosts` steps, and use `just` recipes throughout.
- Marquez loads its own config through `MARQUEZ_CONFIG` and reads its database password from `MARQUEZ_DB_PASSWORD`.
  Its role no longer uses the literal password `marquez`, and its config no longer holds the unused OpenSearch settings.
- Chart templates read the Postgres host from `global.postgresqlHost`, so production's `db-postgresql-primary` works without template edits.
- CI installs the `dbt` extra, so `ty` can resolve the dbt-duckdb plugin's imports.
- On Kubernetes, each database consumer waits in an init container until it can log in to its own database with its own password.
  The chart templates share the `srdp.waitForDbLogin` helper, and the Dagster values carry literal copies because the subchart can't use it.
- `srdp-setup` validates its config strictly: unknown keys, database and role names that aren't lowercase Postgres identifiers of at most 63 characters, and the superuser as a role all fail at startup.
- `srdp-setup` sends role passwords as SCRAM hashes, so a logged statement never holds a plain password.
- `srdp-setup` and the wait containers run as non-root with a read-only filesystem and no capabilities.
- The chart's setup Job runs before Zitadel's hooks, gives up after 2 retries or 4 minutes, and can be switched off with `setup.enabled`.
  It stays after it succeeds, until the next install or upgrade, so `kubectl logs job/srdp-setup` always shows the last run.
  Each connection attempt times out after 5 seconds, so an unreachable host can't use up those 4 minutes.
- `just build-and-push` also builds and pushes the `srdp-setup` image, and stops on the first failed build or push.
- Docker Compose routing moved from `config/traefik/traefik.yml` to `config/traefik/dynamic/srdp.yml`, which takes every hostname from `SRDP_DOMAIN` (#91).
  `traefik.yml` keeps only Traefik's static configuration.
  Compose mounts each routing file on its own, so an overlay can add its own routing file to `/etc/traefik/dynamic/`.
- The Traefik dashboard port 8080 is published by `docker-compose.override.yml` only, so a production run doesn't expose it.

### Removed

- `deploy/docker/initdb/` and the chart's `zitadel-db.primary.initdb.scripts`.
  The `srdp-setup` service replaces both.

### Fixed

- The `ducklake` database is now created at startup, before any consumer connects (#57).
- `docker-compose.prod.yml` serves HTTPS with Let's Encrypt certificates and keeps the Traefik dashboard off (#92).
  It gives Traefik a static configuration of its own, because Traefik ignored the flags it used to pass.
  The local mkcert certificate no longer loads in production, where its absence broke every TLS handshake.
  A production run now stops at startup when `ACME_EMAIL` is missing.
- Git ignores `deploy/docker/letsencrypt/`, where Traefik keeps the Let's Encrypt account and certificate keys.

## [0.3.1] - 2026-09-27

### Fixed

- `README.md` (the content PyPI renders as the project description) rewritten to be accurate and professional. It previously claimed Quarto and dlt as active components, Quarto is optional and disabled by default, dlt isn't integrated yet.
- Duplicated content between `README.md` and `docs/index.md` removed. `README.md` is now a short intro linking to the docs site instead of a second copy of the component table, so the two can't drift out of sync again.

### Added

- `docs/00-about.md`: the original tone-of-voice component roster (credit: Daniel Kapitan), preserved and moved out of `README.md`/`docs/index.md` rather than deleted.

### Documentation

- Branch-naming and CHANGELOG-entry requirements added to `CONTRIBUTING.md`/`AGENTS.md`/the PR template.

## [0.3.0] - 2026-09-27

### Security

- Traefik no longer mounts the Docker socket. Routing moved from Docker label auto-discovery to a static file provider (`config/traefik/traefik.yml`), since a read-only socket mount does not restrict the Docker API reachable through it.

### Added

- OpenLineage bridge emits Dagster run and asset events to Marquez for lineage tracking.
- `cbs-example` project (renamed from `default-etl`): a dbt pipeline, income enrichment assets, a Marimo notebook with a push-based enrichment demo, and a Streamlit dashboard.
- Core FastAPI surface (`srdp.api`): catalog reads, Dagster status, and a bearer-token page for calling the API outside the browser.
- DuckDB UI reverse-proxied through Traefik, with its own background API calls exempted from the SSO redirect rewrite.
- Static hub landing page linking every platform service.
- Idempotent Zitadel OIDC redirect-URI provisioning script (`deploy/docker/provision-oidc.sh`).
- Local Kubernetes testing via `kind`, replacing Colima's built-in k3s.
- DuckLake IO manager wired into both the Helm chart and the Docker Compose deployment targets.
- Release management: `scripts/release.sh`, and GitHub Actions workflows that draft GitHub Releases and publish to PyPI via Trusted Publishing.
- ADRs on RBAC and authentication accepted; other ADRs updated for compliance.

### Changed

- Local domains renamed from `*.local.dev` to `*.srdp.localhost`. The `.localhost` TLD auto-resolves to loopback, so no `/etc/hosts` edit is needed.
- Quarto removed from the default stack.
- Marquez, the API, DuckDB UI, and the hub wired into `docker-compose.yml`, with database passwords externalized.

### Fixed

- Dagster module name corrected in `values-local.yaml`.
- `dagster` made executable in the Dockerfile without `uv run` by adding the venv to `PATH`.

### Documentation

- `cbs-example` README documents the built architecture.
- All `local.dev` references updated to `srdp.localhost`.

### Dependencies

- CVE remediation dependency upgrades.
- `api`, `dbt`, `openlineage`, and `dagster-postgres` extras added, build-system pinned, `python<3.13` pinned.

## [0.2.0] - 2026-06-12

### Changed

- Restructured the repository into a monorepo layout: platform library in `src/srdp/`, client ETL projects in `projects/`, service images in `services/`, and all deployment manifests under `deploy/` (Docker Compose, Helm chart, OpenTofu). Prior layout had these spread across `docker/`, `kubernetes/`, and `docker/apps/`.
- Expanded `just` task runner with recipes for local dev, production deployment, linting, testing, and CI.
- Updated `AGENTS.md` with comprehensive coding conventions for contributors and AI assistants.

### Added

- `src/srdp/io/ducklake.py` provides the DuckLake IO manager, backed by DuckDB and PostgreSQL.
- `src/srdp/io/storage.py` defines the abstract storage backend interface. Only the local filesystem implementation exists so far. Azure and S3 backends are tracked separately.
- `src/srdp/resources/k8s.py` defines Kubernetes resource definitions for Dagster.
- `pyproject.toml` sets up a proper monorepo package with `uv`.
- Architecture decision records (ADRs) in `docs/adr/` covering platform architecture, deployment model, auth, compute, and data organization.
- `.github/instructions/` holds domain-specific coding instructions for Python, Dagster, and deploy targets.
- `SECURITY.md`, `CONTRIBUTING.md`, issue templates, and PR template.

## [0.1.0] - 2024-01-01

Initial release.

[Unreleased]: https://github.com/srdp-hub/srdp/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/srdp-hub/srdp/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/srdp-hub/srdp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/srdp-hub/srdp/compare/0.1.0...v0.2.0
[0.1.0]: https://github.com/srdp-hub/srdp/releases/tag/0.1.0
