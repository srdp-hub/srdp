---
status: accepted
date: 2026-06-08
revised: 2026-10-03
decision-makers: Yannick Vinkesteijn
---

# Data organization and ingestion

> **Proposed revision** in [ADR-0012](./0012-dlt-alongside-dagster.md) (#84): raw append-only and validation before skipping the landing zone are rules on by default, which a project can switch off explicitly in `srdp.toml`.

## Context and Problem Statement

SRDP needs a consistent data organization model that connects three layers:

1. Storage: where data files live (local filesystem, cloud object storage).
2. Catalog: how data is registered and discoverable (DuckLake catalogs, schemas, and tables in PostgreSQL).
3. Orchestration: how data is produced and consumed (Dagster assets and the IO manager).

These three must stay aligned. If they diverge, data exists on storage that no catalog knows about, or assets produce outputs that cannot be found. Client projects need clear conventions for how their data is structured and how new data enters the platform.

This ADR addresses three questions:

1. Data organization: how are layers, schemas, and tables structured, and what is fixed versus configurable?
2. Write semantics: what invariants govern how data is written and corrected, without prescribing each asset's transformation logic?
3. Ingestion: how does data enter the platform, and what guards its integrity on the way in?

### Constraint: DuckDB three-level naming

DuckDB supports exactly three levels: `catalog.schema.table`. There is no nested schema support. [ADR-0006](./0006-deployment-and-project-isolation-model.md) assigns these levels: catalog = project, schema = layer, table = entity. The IO manager maps Dagster asset keys into the schema and table levels, and the project supplies the catalog from its code location.

## Considered Options

### Data organization

1. Configurable medallion layers: a recommended default progression (landing, raw, curated) that projects can rename or extend, organized on medallion principles.
2. Domain-based schemas: organize by domain with no enforced progression.
3. Flat structure: all tables in one schema, differentiated by naming only.

### Write semantics

1. Fix invariants and a write-mode menu in the ADR; leave per-asset derivation to asset code.
2. Fix a single global write mode (for example always full replace).
3. Leave write semantics entirely to each asset, with no platform invariant.

### Ingestion

1. Landing zone first: a pre-catalog drop area gatekeeps data into the first lake-managed layer; the API and Dagster are the controlled entry points.
2. Direct-to-catalog: sources write straight into lake-managed layers.
3. API only: no file-drop path.

## Decision Outcome

### Data organization: configurable medallion layers

Chosen option: "Configurable medallion layers". The platform distinguishes what it fixes from what client projects decide.

Fixed by the platform:
- The three-level naming `catalog.schema.table` (a DuckDB constraint), assigned as project, layer, entity (see [ADR-0006](./0006-deployment-and-project-isolation-model.md)).
- IO manager key mapping: the first asset-key segment is the schema (layer), remaining segments form the table.
- All writes go through the IO manager into DuckLake (see [ADR-0003](./0003-data-catalog-lineage-and-observability.md)).
- The API discovers data dynamically from the catalog; no structure is hardcoded in the API.

Configurable by client projects:
- Zone names. The defaults below are a recommendation, not a requirement; projects may name their zones as they wish, but the organization should follow medallion principles (increasing validation and refinement across layers).
- How tables are named within a layer.
- Whether to add further layers (for example `aggregated`, `reference`).
- Which ingestion paths to use.

Default layers:

| Layer | DuckLake schema | Purpose |
|:---|:---|:---|
| Landing | (pre-catalog) | Unvalidated drop area, not DuckLake-managed; the ingestion gatekeeper |
| Raw | `raw` | First lake-managed layer: source-faithful, schema and types validated |
| Curated | `curated` | Business-rule validated, enriched, joined; reproducible from raw |

### Asset key to catalog mapping

Asset keys use 2–3 lowercase segments: [layer, table] or [layer, domain, table]. DuckLake uses a flat catalog.schema.table hierarchy. The catalog comes from the project (see ADR-0006), so only schema and table remain. The first segment becomes the schema; remaining segments joined with _ form the table name.

| Dagster asset key              | Catalog (project)  | Schema  | Table        |
|--------------------------------|--------------------|---------|--------------|
| ["raw", "orders"]              | from code location | raw     | orders       |
| ["raw", "sales", "orders"]     | from code location | raw     | sales_orders |
| ["curated", "sales", "orders"] | from code location | curated | sales_orders |

DuckLake manages the filesystem layout within the project's storage prefix automatically (UUID-named Parquet files, copy-on-write). There is no manual file management.

### Write semantics: invariants and a write-mode menu

Chosen option: "Fix invariants and a write-mode menu; leave per-asset derivation to asset code." The ADR fixes what must always hold and the menu of supported modes; how a given asset derives its data is implementation and stays in the asset.

Invariants:
- Raw is append-only by default, so pipelines don't mutate it in place, and new or corrected data arrives as new appends or snapshots.
  Raw is the source of truth and the audit trail.
  A project can switch this off explicitly in `srdp.toml` for its own raw tables, and then loses the audit trail and the rebuild from raw for those tables.
- Curated layers are reproducible from raw. No information lives only in curated; external reference and enrichment data must itself be ingested as a source.

Write-mode menu: the IO manager supports `replace`, `append`, and `merge`. Each asset chooses its mode based on size and cost. Raw's append-only default constrains the menu for raw: corrections are represented as new appends or snapshots, never as a destructive in-place `replace` of prior raw data. Curated layers may use any mode, since they are reproducible from raw. Cost-aware guidance (partitioning, incremental merge, compaction, snapshot retention) lives in the docs, not this ADR.


Immutability is logical, not physical-write-once. Raw is a lakehouse table format over object storage, not a relational database. "No in-place pipeline mutation" is fully compatible with DuckLake's recommended maintenance: compaction (`ducklake_merge_adjacent_files`) and retention (`ducklake_expire_snapshots`, `ducklake_cleanup_old_files`, `ducklake_delete_orphaned_files`, via `CHECKPOINT`). These rewrite and garbage-collect physical files while preserving logical table state.

Consequence: reproducibility and audit reach back only as far as the snapshot-retention window. Retention depth is a per-deployment policy knob (long for audit-heavy deployments, short for storage- or erasure-sensitive ones).

Erasure (GDPR): when personal data is in scope, erasure is supported through DuckLake's maintenance machinery (logical delete, then expire snapshots, then cleanup old files). This is a separate, audited administrative operation, not a pipeline write. The specific erasure strategy (direct deletion, crypto-shredding, or other techniques) is a per-deployment decision with its own implementation requirements.

### Ingestion: landing zone first

Chosen option: "Landing zone first". A landing zone sits before raw. External APIs and other sources drop unvalidated files or data into the landing zone, which is not DuckLake-managed. The landing zone does two jobs:

- Decouples ingestion from processing: data can be picked up by Dagster immediately or processed asynchronously.
- Gatekeeps: schema checks, file and content standardization, so only technically processable data is admitted into the lake.

Raw is the first lake-managed, monitored layer and is reachable only by passing the landing-zone gate or the API. So the controlled entry points are the landing-zone gate and the API, and everything from raw onward is platform-protected and watched for out-of-band change (see [ADR-0003](./0003-data-catalog-lineage-and-observability.md)).

All writes to lake-managed layers go through Dagster by default (see [ADR-0002](./0002-api-and-access-strategy.md), [ADR-0007](./0007-compute-and-scaling.md)). The API's write role is limited to accepting landing-zone drops, which trigger Dagster jobs, plus explicitly-defined operational endpoints. The landing zone is required for external and untrusted ingress, because that is where unconforming data is caught before it can break DuckLake management, monitoring, and pipeline visibility. It may be bypassed only by a direct Dagster source asset that applies equivalent validation (schema and content checks) before writing raw; the gatekeeping contract holds either way.
A project can switch this validation off explicitly in `srdp.toml`, which keeps the relaxation visible.

Ingestion building blocks the platform provides:
- API ingestion: data pushed through the FastAPI API, landed, and processed by a triggered Dagster job.
- Landing zone: files dropped into a configurable location; a Dagster sensor detects and processes them.
- Direct asset loading: a client asset loads from any source and returns a dataframe; the IO manager handles the rest.

### Out of scope: per-project concerns

Schema evolution and data quality are supported by the platform but governed by client projects. DuckLake supports schema evolution (additive columns, type widening) and Dagster provides asset checks that can gate promotion between layers. The specific policies (which schema changes are accepted, what quality checks run, what blocks promotion from raw to curated) are per-project decisions.

### Consequences

- Good, because the default layers give projects a clear progression while remaining renameable and extensible.
- Good, because the IO manager enforces alignment between asset keys, catalog entries, and storage paths, so no name divergence is possible.
- Good, because write semantics are fixed at the right altitude (invariants and a menu), leaving derivation to assets.
- Good, because append-only raw, on by default, gives reproducibility and an audit trail, and stays compatible with compaction and retention.
- Good, because the landing zone gatekeeps integrity and decouples ingestion from processing.
- Good, because erasure is a defined, audited capability rather than a contradiction of immutability.
- Bad, because DuckDB's three-level naming means domains must be encoded in the table name when layers are used (`raw.sales_orders`, not `raw.sales.orders`).
- Bad, because asset renames create new DuckLake tables; old tables remain until explicitly dropped.
- Bad, because landing-zone ingestion needs a Dagster sensor per source pattern, adding configuration per integration.

## Pros and Cons of the Options

### Domain-based schemas

- Good, because data maps directly to business domains.
- Bad, because without a progression, validation state is implicit and inconsistent across projects.

### Flat structure

- Good, because simplest possible layout.
- Bad, because it relies entirely on naming discipline and loses the medallion progression.

### Single global write mode

- Good, because one rule to learn.
- Bad, because full replace is needlessly expensive for large datasets and append-only does not fit curated rebuilds; the right mode is per-asset.

### Direct-to-catalog ingestion

- Good, because fewer moving parts.
- Bad, because non-conforming data reaches lake-managed layers, breaking catalog management and monitoring, which is exactly what the landing zone prevents.
