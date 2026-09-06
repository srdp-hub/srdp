---
status: accepted
date: 2026-06-08
decision-makers: Yannick Vinkesteijn
---

# Deployment and project isolation model

## Context and Problem Statement

SRDP is a self-hostable data platform. A running instance holds data from multiple sources, the pipelines that process it, and the users who consume it. Two questions need a clear architectural answer before authorization (see [ADR-0005](./0005-authorization-and-data-access.md)) and the catalog layout (see [ADR-0003](./0003-data-catalog-lineage-and-observability.md), [ADR-0004](./0004-data-organization-and-ingestion.md)) can be finalized:

1. What is the unit of isolation? When SRDP serves more than one customer or domain, where is the boundary that separates one body of data from another?
2. How is data subdivided inside a single instance? A team's working set of sources and pipelines needs its own access boundary and its own slice of the catalog, without giving every team a full copy of the platform.

The answer drives the DuckLake catalog layout, the IO manager's asset-key mapping, and the granularity of access grants. It cannot stay implicit.

## Considered Options

1. Deployment is the isolation unit; a project is its own DuckLake catalog. One deployment is one data lake (sources, pipelines, users). Multiple customers means multiple deployments. Inside a deployment, data is divided into projects, and each project is a separate DuckLake catalog (its own metadata schema and storage prefix).
2. Single shared catalog per deployment; projects separated by a schema-naming convention. One catalog holds every project, kept apart by prefixing schema names.
3. Tenant partitioning within a shared data plane. One catalog and shared tables, with tenants separated by row or column scoping enforced at query time.

## Decision Outcome

Chosen option: "Deployment is the isolation unit; a project is its own DuckLake catalog", because it gives a hard isolation boundary between customers without a multi-tenant security model, and a clean, native subdivision inside a deployment that maps directly onto DuckDB's three-level naming. DuckLake and DuckDB have no per-row or per-column security, so any in-data-plane tenant separation (option 3) would have to be enforced entirely in application code over shared tables, which is both fragile and the wrong place for an isolation boundary.

> **Isolation vs. minimization.** The rejection above is about the *isolation* boundary between customers and projects, which stays hard (separate catalogs, separate deployments). It does **not** rule out fine-grained data *minimization* within a project, meaning which columns and rows a given consumer may see. [ADR-0008](./0008-identity-propagation-and-data-contracts.md) adds that via a constrained evaluator on mediated read paths.
>
> The two are different jobs. The evaluator is defence-in-depth inside the catalog boundary, never a replacement for it. A bug in it leaks within-project data to within-project principals, not across tenants. Option 3 was rejected because it would make row/column scoping the isolation boundary, where that fragility is unacceptable. As a minimization layer, it is not load-bearing for isolation.

### Deployment as the isolation unit

One deployment is one data lake: a single set of sources, pipelines, and users that belong together. This keeps the strongest boundary (separate processes, separate storage, separate identity stack) between bodies of data that must not mix, and it means the platform never has to enforce cross-customer isolation inside a shared query engine.

**Multi-tenant deployments are the owner's choice.** A deployment is owned by one tenant (the deployment owner), which decides whether to stay single-tenant or admit additional tenants (see [ADR-0005](./0005-authorization-and-data-access.md)). Single-tenant per deployment remains the recommended default and the only configuration that gives hard isolation. When the owner admits multiple tenants, they are separated *inside* the deployment by catalog/project boundaries plus RBAC (and data contracts for shared data), which is softer than separate deployments; parties that must be hard-isolated still get their own deployments. This refines "serving multiple customers means multiple deployments" into a recommended default rather than a hard requirement, and does not reintroduce option 3: in-deployment separation is by catalog/project, never by row/column scoping over shared tables.

### Project as a DuckLake catalog

Inside a deployment, data is divided into projects. A project is a team's working set of sources and pipelines, and it is the unit of access (see [ADR-0005](./0005-authorization-and-data-access.md)). Each project is its own DuckLake catalog:

- its own metadata schema in the shared PostgreSQL catalog database (for example `ducklake_sales`),
- its own storage prefix (for example `DATA_PATH/sales/`),
- its own lifecycle: it can be created, granted, backed up, and retired independently.

A project maps onto a Dagster code location. Client projects live in their own repositories and import `srdp` as a dependency (see [ADR-0001](./0001-platform-architecture-and-distribution.md)); `projects/cbs-example/` in the srdp repo is a reference example, not where client code lives. Each project's IO manager resource is configured with that project's catalog, so the catalog is supplied by the code location, not encoded in every asset key.

### Mapping onto DuckDB three-level naming

DuckDB supports exactly three levels: `catalog.schema.table`. The model uses all three:

| Level | Maps to | Example |
|:---|:---|:---|
| catalog | project | `sales` |
| schema | medallion layer | `raw` |
| table | entity | `orders` |

So a fully qualified table is `project.layer.entity`, for example `sales.raw.orders`. The asset-key-to-`schema.table` mapping in [ADR-0004](./0004-data-organization-and-ingestion.md) is unchanged; the catalog dimension is added from the project's code location and IO manager configuration rather than from the asset key. A single-project deployment (such as the `projects/cbs-example/` reference) is just one catalog.

### Multiple catalogs

DuckDB natively supports attaching multiple catalogs in a single connection. While the default model is one catalog per project, a deployment can attach additional catalogs as needed: a shared reference catalog (country codes, lookup tables), a catalog per medallion zone, or any other arrangement. This is deployment configuration, not a code change. The platform does not restrict how catalogs are organized beyond the default recommendation.

### Access and the read path

Access grants are scoped to projects (see [ADR-0005](./0005-authorization-and-data-access.md)). A user's read connection attaches only the project catalogs they are permitted to read, read-only, which is how project isolation is enforced at query time (see [ADR-0002](./0002-api-and-access-strategy.md) for the read and write split). Because catalogs are separate DuckLake attachments, granting or revoking a project is attaching or detaching a catalog, not rewriting row-level policies.

### Consequences

- Good, because customer isolation is a hard boundary (separate deployment) rather than an application-enforced policy over shared data.
- Good, because project equals catalog is a clean grant boundary: access is attach or detach, with no per-row or per-column rules to maintain in an engine that does not support them.
- Good, because the model fits DuckDB's three levels exactly (project, layer, entity) with no naming contortions.
- Good, because projects map to Dagster code locations, so the catalog dimension stays out of asset keys.
- Good, because per-project lifecycle (create, back up, retire, set retention) follows naturally from per-project catalogs.
- Bad, because serving many small customers means many deployments to operate; this is a deliberate trade of per-instance overhead for isolation simplicity.
- Bad, because cross-project queries inside a deployment require attaching multiple catalogs and cannot assume a single namespace; shared reference data must be handled explicitly (for example a dedicated shared-reference catalog) rather than implicitly joined.

## Pros and Cons of the Options

### Single shared catalog, schema-name convention

- Good, because it is the least infrastructure: one catalog, one storage prefix.
- Bad, because the project boundary becomes a naming convention rather than an enforced boundary, so a misconfigured grant or query can cross it.
- Bad, because it consumes the schema level for project separation, leaving only two levels for layer and entity and forcing the medallion layers into table-name prefixes.

### Tenant partitioning within a shared data plane

- Good, because a single instance could serve many tenants with less per-tenant overhead.
- Bad, because DuckLake and DuckDB have no native row or column security, so the entire isolation boundary would live in application code over shared tables.
- Bad, because it puts the platform's hardest security guarantee in its most fragile place, and a single query bug becomes a cross-tenant data leak.
