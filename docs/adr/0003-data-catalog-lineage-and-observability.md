---
status: accepted
date: 2026-06-08
revised: 2026-10-03
decision-makers: Yannick Vinkesteijn
---

# Data catalog, lineage, and observability

> **Proposed revision** in [ADR-0012](./0012-dlt-alongside-dagster.md) (#84): the IO manager stays the default write path, and other writers are allowed only when explicitly permitted, under the conditions in "Write path" below.

## Context and Problem Statement

A running SRDP instance needs to answer three categories of questions:

1. Data observability: what data exists, where did it come from, what transformed it, and is it current? Without an enforced write path, data can change unobserved: files appear on storage that no catalog knows about, transformations run without lineage, and no one can answer "what happened to this dataset."
2. Functional observability: are services running, are pipelines succeeding, and what errors occurred?
3. Resilience and integrity: does the platform keep working when a component fails, and how does it detect data that changed outside its control?

These concerns require different mechanisms. This ADR decides all three, including the parts an earlier draft deferred: where logs live, what the lineage backend is, and how the platform survives partial failure.

## Considered Options

### Data observability: catalog and IO layer

1. DuckLake (mandatory IO wrapper): all data writes go through DuckLake via the Dagster IO manager. Every change is automatically cataloged, versioned, and discoverable.
2. Apache Iceberg + REST catalog: industry-standard open table format with broad engine support.
3. No mandatory catalog: let services and assets write data directly, track metadata separately.

### Lineage

1. OpenLineage as a core feature, emitted across the stack and collected by a backend.
2. Dagster-native lineage only: rely on Dagster's built-in asset graph.

### Logs and metrics

1. One tool for everything (for example ship all signals to a single stack).
2. Layered model: separate sinks for audit logs, transformation logs, application logs, and metrics, matched to each signal's durability and query needs.

## Decision Outcome

### Data observability: DuckLake as mandatory IO wrapper

Chosen option: "DuckLake as mandatory IO wrapper", because it makes data observability automatic rather than opt-in. By routing all writes through the Dagster IO manager and into DuckLake, every data change is cataloged, versioned, and discoverable without any action from the asset author.

This is a core architectural decision: DuckLake is not optional. It is the mechanism that ensures no lake-managed data exists outside the catalog; the landing zone is governed separately as a pre-catalog ingress area (see [ADR-0004](./0004-data-organization-and-ingestion.md)). DuckLake stores catalog metadata in PostgreSQL, which the platform already runs, so there is no separate catalog server. Data is written as standard Parquet files to any storage backend, and DuckDB serves as the in-process query engine.

The IO wrapper is agnostic to how data is produced. Asset authors can use Polars, pandas, DuckDB SQL, or any other tool. What is mandatory is that every output lands in DuckLake's catalog, through the IO manager by default.

### Why DuckLake over Iceberg

The decisive reason is single-node operational simplicity. SRDP's design point is a self-hostable platform that runs the same way on a laptop and a single production VM. DuckLake keeps its catalog in the PostgreSQL the platform already runs and queries data in-process through DuckDB, so there is no extra server to deploy, secure, or back up. Iceberg can be deployed in many ways, but those deployments are typically more complex and less optimized for this single-node target, and most require additional services and resources (a catalog service, and often more) whose value is broad multi-engine, distributed access that SRDP does not need. DuckLake has proven efficient in-process and adds no services beyond the PostgreSQL already present: it is the simple, easy option for this design point. The trade accepted is that fewer external tools can read DuckLake metadata natively today.

### Write path: Dagster IO manager

The IO manager (`srdp.io.ducklake`) is the default write path into DuckLake. Asset code contains no storage logic; the IO manager handles persistence and catalog registration automatically. The asset-key-to-catalog mapping is specified in [ADR-0004](./0004-data-organization-and-ingestion.md), and the catalog dimension (project) in [ADR-0006](./0006-deployment-and-project-isolation-model.md).

Other writers, such as dbt or dlt, are only permitted explicitly, each with its own decision (for dlt, [ADR-0012](./0012-dlt-alongside-dagster.md)).
A permitted writer runs inside Dagster, writes through DuckLake's catalog with the platform's catalog settings, and keeps the catalog and lineage complete.
It may not affect the ability of DuckLake, Dagster or any other core component to do its work.

Why an IO manager, not a Dagster resource? A resource requires every asset to explicitly call `ducklake.write(...)`; a developer forgetting that call means data exists in the pipeline but not in the catalog. The IO manager makes catalog registration automatic and impossible to skip.

### Storage backends

The `StorageBackend` abstract base class (`srdp.io.storage`) decouples DuckLake from storage providers. Implementing a new backend requires two methods: `get_base_path()` and `configure_duckdb()`. Built-in: local filesystem. Optional via extras: Azure Blob Storage, S3-compatible storage.

### Lineage: Dagster-native baseline, OpenLineage into Marquez

Chosen option: "OpenLineage as a core feature", with a staged rollout. DuckLake tracks what data exists and how it changed, but not the full flow across systems. Lineage is captured at the Dagster asset boundary, not inside each library, so coverage does not depend on whether the tools used inside an asset support OpenLineage natively.

Two honest caveats shape delivery, and correct an earlier "near-free" framing:

- **Emission is a platform-provided bridge, not native.** The Dagster OpenLineage integration is unmaintained, so SRDP emits OpenLineage events from a small bridge: a run-status sensor that translates Dagster materializations into OpenLineage RunEvents via `openlineage-python`. This is platform code regardless of backend.
- **The backend is Marquez, not a bespoke store.** There is no off-the-shelf OpenLineage-to-PostgreSQL store; Marquez *is* the Postgres-backed OpenLineage reference collector. SRDP therefore aims to use **Marquez** as the OpenLineage backend rather than building a custom event store. Marquez is an added (optional) service with its own PostgreSQL database, accepted as the cost of real, queryable lineage with a UI.

Baseline: **Dagster's built-in asset catalog** is the lineage browsing surface out of the box, with no extra service. The OpenLineage bridge into Marquez is the richer tier, enabled when cross-system lineage or a dedicated lineage UI is needed.

| Source | How lineage is captured |
|:---|:---|
| Dagster assets | OpenLineage emission at the asset boundary via the platform bridge (a run-status sensor); the baseline guarantee |
| Asset metadata | Authors attach lineage and schema as Dagster asset metadata, filled dynamically (for example from Polars or other dataframe metadata) or set explicitly, so libraries with no native OpenLineage support are still tracked |
| DuckDB / DuckLake | Finer-grained lineage via the lineage extension where available |
| External systems | OpenLineage Python client for processes that run outside Dagster |

Because lineage rides on Dagster asset metadata, a library lacking native OpenLineage support is not a gap: the asset still emits lineage, dynamically derived or explicitly set. Dagster's built-in asset catalog remains the primary browsing interface for data discovery; OpenLineage augments it with cross-system lineage, it does not replace it.

### Functional observability: a layered model

Logs and metrics are different signals and use different tools. Prometheus is a metrics system, not a logging collector. The platform uses a layered model, and the metrics layer is pluggable to match the core-vs-optional boundary in [ADR-0001](./0001-platform-architecture-and-distribution.md).

| Signal | Sink | Notes |
|:---|:---|:---|
| Audit and access logs | Append-only JSONL or Parquet on blob storage | Decoupled from the services they monitor: survives outages of PostgreSQL, Dagster, and the query engine as long as the storage sink is reachable; DuckDB-queryable later |
| Transformation and pipeline logs | Dagster | `context.log` and `logging`, event log in PostgreSQL, compute-log manager ships to blob |
| Application logs | stdout and stderr | Aggregated by the hosting environment (Docker log driver, Kubernetes tooling) |
| Metrics and alerting | Prometheus + Grafana (+ Alertmanager) | Optional: self-host by default, managed endpoint as an option |

Instrumentation is via OpenTelemetry so the metrics and tracing backend stays swappable. The responsibility split is: Dagster owns transformation logs, blob audit owns API and access logs, Prometheus owns metrics.

### Resilience and data integrity

The platform commits to a resilience posture and to detecting data that changes outside its control.

Failure-mode matrix:

| Component down | What still works | What stops |
|:---|:---|:---|
| PostgreSQL (catalog) | Blob audit logs; read sessions already open against attached catalogs, where their metadata is cached | New writes, new catalog reads and new read sessions; Dagster runs |
| Dagster | Direct reads of existing data; the API read path | Pipelines, materializations, scheduled jobs |
| Storage backend (blob) | Catalog metadata browsing | Reading and writing actual data |
| Lineage backend | Everything except lineage capture | New lineage events (buffered or dropped, not blocking pipelines) |
| Metrics stack | Everything | Dashboards and alerting |

PostgreSQL holds the DuckLake catalog and is the platform's single most critical component. The catalog points at managed Parquet snapshots in storage (logically immutable, not physically write-once; see [ADR-0004](./0004-data-organization-and-ingestion.md)), so the data itself is not lost if the catalog is, but the catalog must be recoverable (backup and restore is an operational runbook, not an ADR). Whether the catalog can be rebuilt from storage alone is bounded by what DuckLake metadata is reconstructable; the platform treats PostgreSQL as the source of truth and protects it with backups rather than relying on reconstruction.

Data integrity: storage locations are reachable only through the platform, and the platform detects and alerts on out-of-band changes (catalog-versus-storage drift), distinct from service health. Detection is a mix of cheap in-job reconciliation during or after writes and a periodic full reconciliation that compares the DuckLake catalog against actual storage and alerts on divergence.

### Consequences

- Good, because data observability is automatic: every write to a lake-managed layer goes through DuckLake, so no lake-managed data exists outside the catalog.
- Good, because the IO wrapper is tool-agnostic: asset authors choose their own dataframe library.
- Good, because the catalog needs no extra server, and DuckDB is in-process.
- Good, because lineage is decided (Dagster-native asset catalog baseline; OpenLineage into Marquez for richer or cross-system lineage) rather than left planned.
- Bad, because OpenLineage emission needs a platform-built Dagster bridge (the Dagster integration is unmaintained), and Marquez is an added service with its own database, so lineage beyond the Dagster-native baseline is not free.
- Good, because audit logs are decoupled from the services they monitor, so they remain available through outages of the catalog, orchestrator, or query engine.
- Good, because the failure-mode matrix and drift detection make resilience and integrity explicit architectural properties.
- Bad, because DuckLake is newer than Iceberg, so fewer external tools can read its metadata natively.

## Pros and Cons of the Options

### Apache Iceberg + REST catalog

- Good, because widest engine support (Spark, Trino, Flink, Snowflake, BigQuery).
- Bad, because the REST catalog is an additional service to deploy and operate.
- Bad, because its deployments are typically more complex and less optimized for a single-node target; its value is broad multi-engine, distributed access that SRDP does not need.

### No mandatory catalog

- Good, because no IO wrapper overhead; services write data directly.
- Bad, because data changes are unobserved unless each service implements its own tracking.
- Bad, because no single source of truth for what data exists.

### Dagster-native lineage only

- Good, because zero additional dependencies.
- Bad, because lineage is only visible inside the Dagster UI, not portable across systems.
- Bad, because DuckDB queries executed outside Dagster produce no lineage.

### One tool for everything

- Good, because a single pipeline to operate.
- Bad, because logs and metrics have different durability and query needs; a single sink is either too heavy for logs or too weak for metrics, and tends to fail together with the rest of the stack.
