---
status: accepted
date: 2026-06-05
decision-makers: Yannick Vinkesteijn
---

# Platform architecture and distribution

## Context and Problem Statement

SRDP is a composable data platform that assembles open-source components into a single deployable stack. The platform itself contains no business logic: it is an empty shell that client projects fill with their own data pipelines, assets, and transformations.

This ADR addresses two questions:

1. Distribution: how is the platform packaged, installed, and deployed? The primary path is installing `srdp` as a package dependency, requiring no platform engineering expertise. Advanced users may also fork or template the repository for full control over the platform internals. Both modes must work on a developer laptop and a production cluster with the same service topology.
2. Extension: how do clients get their own pipeline code into a running SRDP instance, and how do they deploy it?

## Considered Options

1. Single `srdp` PyPI package with optional extras, Docker Compose and Helm as deployment targets. Clients build custom images extending srdp base images to deploy their pipeline code.
2. uv workspace: clients clone the `srdp` repo and add their project as a workspace member, each with its own `pyproject.toml`.
3. CLI-driven (`srdp init / dev / deploy`): a wrapper that scaffolds client projects, builds images, and manages deployment. Bare Docker Compose and Helm remain available for advanced users.

## Decision Outcome

Chosen option: "Single package with base images", because it minimises the number of things a customer needs to understand while supporting the full range from laptop to production. The CLI (option 3) is under consideration as a future abstraction layer on top of this foundation.

### Platform layers

The platform is composed of four layers:

Core library (`src/srdp/`): the Python package clients install. Contains IO managers, resources, base asset patterns, and the FastAPI API server. This is what `uv add srdp` provides.

Core infrastructure services (containers, always deployed): PostgreSQL, Traefik, Dagster (daemon, code server, and the internal webserver that serves GraphQL), Zitadel, and oauth2-proxy. These form the platform runtime and are deployed via Docker Compose or Helm. They are not Python packages. The Dagster webserver runs as core because the FastAPI API depends on its GraphQL endpoint internally (see [ADR-0002](./0002-api-and-access-strategy.md)); exposing the Dagster UI route externally through Traefik is the optional part. Lineage browsing uses Dagster's built-in asset catalog by default, which needs no additional service (see [ADR-0003](./0003-data-catalog-lineage-and-observability.md)).

Optional services (containers, added as needed): the externally-exposed Dagster UI route, marimo, quarto, the metrics and alerting stack (Prometheus, Grafana, Alertmanager), the Marquez lineage backend (see [ADR-0003](./0003-data-catalog-lineage-and-observability.md) for both), and potentially others. These extend the platform with additional capabilities. Some may import `srdp` (e.g. through a Python client), others are standalone. The platform functions without any of these deployed.

Optional extras (Python dependencies, environment-specific): cloud storage backends and deployment-target-specific dependencies. These are genuine optional extras because the platform functions without them on a default local deployment.

```
uv add srdp                # core library (Dagster, Polars, DuckDB, FastAPI)
uv add srdp[azure]         # + Azure Blob Storage backend
uv add srdp[k8s]           # + Kubernetes executor (dagster-k8s)
```

### Platform deployment

There are two deployment targets, Docker Compose and Helm:

| Target | Location | Use case |
|:---|:---|:---|
| Docker Compose | `deploy/docker/` | Local development, single-node production VMs |
| Helm chart | `deploy/kubernetes/srdp-chart/` | Kubernetes clusters |

Both define the same logical set of services (core infrastructure + optional services). Adding a new service means adding a container definition in both places with standard Traefik labels/annotations (see [ADR-0002](./0002-api-and-access-strategy.md)).

OpenTofu (`deploy/opentofu/`) is provisioning tooling, not a deployment target. Like `just`, it is a tool the platform uses, here to stand up the cloud infrastructure (VMs, networks) that a Compose or Helm deployment then runs on. It carries no platform services of its own and is out of scope for this decision.

### Client project deployment

A client project is a separate repository that imports `srdp` as a dependency and contains pipeline code (Dagster assets, dlt sources, transformations). To deploy, the client builds a Docker image extending an srdp base image:

```dockerfile
FROM srdp/dagster-worker:latest
COPY . /app
RUN uv sync
```

The resulting image contains both the platform library and the client's assets. It slots into the existing Docker Compose or Helm deployment by replacing the default worker image.

`projects/cbs-example/` in this repo serves as a reference implementation and starting template for client projects.

### State and persistence

Containers and pods are stateless and rebuildable from their image plus Git; they hold no durable state. All durable state lives in two surfaces, both reachable by configuration so the platform never assumes co-location with a service:

- PostgreSQL: the DuckLake catalog, the Dagster run and event log, and Zitadel identity data. The optional Marquez lineage backend runs its own separate database (see [ADR-0003](./0003-data-catalog-lineage-and-observability.md)).
- Object storage (or a mounted data volume): DuckLake Parquet files, the landing zone, append-only audit and access logs, and shipped Dagster compute logs.

Everything else is ephemeral. Project caches, DuckDB spill, and scratch are disposable and rebuildable, so they sit on local scratch (`emptyDir` or tmpfs) and are never backed up. Application logs go to stdout for the host or cluster to aggregate; durable audit logs and compute logs are already externalized to blob (see [ADR-0003](./0003-data-catalog-lineage-and-observability.md)).

PostgreSQL and object storage are externalizable dependencies. The platform connects to each by config (a connection URL, a `StorageBackend`), so where they physically run is a deployment choice, not a code change. The defaults are bundled for a self-contained install that runs the same on a laptop and a single VM: a Postgres container and the local-filesystem backend. For production the recommendation is to point at managed equivalents: an external or managed PostgreSQL (it decouples the most critical state from the cluster lifecycle and offloads failover, patching, and point-in-time recovery) and object storage for the lake. On Kubernetes durable state uses PersistentVolumeClaims and StatefulSets rather than host bind mounts; pushing the lake to object storage leaves the cluster with minimal node-local state.

Backup scope is therefore PostgreSQL and object storage, never container images or code. Backup and restore mechanics are an operational runbook, not part of this decision.

### Versioning and compatibility

`srdp` is versioned with semantic versioning over an explicit public API surface. The public surface is the contract client projects depend on:

- the IO manager and its asset-key-to-catalog mapping (see [ADR-0004](./0004-data-organization-and-ingestion.md), [ADR-0006](./0006-deployment-and-project-isolation-model.md)),
- the platform resources,
- the `StorageBackend` interface (`srdp.io.storage`).

A breaking change to that surface bumps the major version. Internal implementation details outside this surface may change in minor or patch releases. Client projects pin a major version of `srdp` and upgrade deliberately. Anything not listed above is not part of the contract and carries no compatibility guarantee.

The list above is the Python library contract. The FastAPI HTTP surface that external consumers call (see [ADR-0002](./0002-api-and-access-strategy.md)) is a separate contract, versioned through its published OpenAPI specification and an API version prefix rather than through the library's semantic versioning. The two evolve independently.

### Extension model

Data layer: what the platform ingests, transforms, and exposes. Extensions live in client projects and import from `srdp`. No changes to the `srdp` repo required.

- Add a data source: define a dlt pipeline, register it as a Dagster asset.
- Add a transformation: write a Dagster asset that reads from DuckLake and writes back through the IO manager.
- Add a compute profile: use the K8s resource profiles in `srdp.resources.k8s` as job tags.

Service layer: what infrastructure or tooling services the platform runs. Extensions require a change to the `srdp` repo.

- Add a service: add a container to Docker Compose / Helm with Traefik middleware labels.
- Add a storage backend: implement the `StorageBackend` interface (`srdp.io.storage`), which requires two methods: `get_base_path()` and `configure_duckdb()`.

### Consequences

- Good, because one package to install, version, and document. Client projects have a single dependency.
- Good, because Docker Compose and Helm mirror the same topology, with no conceptual gap between environments.
- Good, because client projects are cleanly external with their own repo, lockfile, and release cycle.
- Good, because base images give clients a standard deployment mechanism without requiring platform expertise.
- Good, because optional extras are genuinely optional (cloud backends, k8s executor), not functionally mandatory.
- Good, because the public API surface is explicit and semantic versioning-governed, so client projects know exactly what they may depend on and what a major upgrade can break.
- Bad, because service config changes must be applied in both Docker Compose and Helm. This is mitigated by sharing configuration through external env and config files and by a parity smoke test that asserts both stacks expose the same logical set of services, but structural changes (adding a service, changing port mappings) still require updates in both places.
- Bad, because without a CLI, clients must understand Docker image builds and Compose/Helm configuration to deploy.

## Pros and Cons of the Options

### uv workspace

- Good, because client projects can resolve against the platform source directly, with no published package needed.
- Bad, because clients must clone the full `srdp` repo to start working.
- Bad, because workspace membership couples client release cycles to the platform repo, making independent versioning difficult.

### CLI-driven

- Good, because it lowers the barrier to entry for clients with no Docker or Kubernetes experience.
- Good, because it can enforce conventions (project structure, image naming, deployment config).
- Bad, because the CLI becomes a critical dependency that must be maintained alongside the platform.
- Bad, because it adds an abstraction layer that advanced users may need to bypass, creating two paths to support.
