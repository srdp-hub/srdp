---
status: proposed
date: 2026-10-02
decision-makers: Yannick Vinkesteijn
---

# dlt alongside Dagster

## Context and Problem Statement

dlt is a good fit for extraction: connectors, schema inference, incremental cursors.
It also overlaps with Dagster and DuckLake: it tracks its own loads and state, and it can write to a destination on its own.
Used carelessly it bypasses Dagster's IO manager, which [ADR-0003](./0003-data-catalog-lineage-and-observability.md) names as the single write path, so loads have no asset, no metadata and no lineage in Marquez.

How do we use dlt without breaking Dagster's view of the data, lineage, or DuckLake's storage?

## Considered Options

1. dlt into DuckLake under strict rules: dlt writes through its `ducklake` destination, inside a Dagster asset.
2. dlt into the landing zone: dlt writes to the landing zone only, and Dagster promotes landing to raw through the IO manager.
3. No dlt: every source is hand-written as a Dagster asset.

## Decision Outcome

Chosen option: "dlt into the landing zone" and "dlt into DuckLake under strict rules", both allowed, because each path carries only the rules it needs.
Into the landing zone dlt can't harm DuckLake, so it's free there.
Into DuckLake it becomes a second write path, so it's strict there.

### Path 1: into the landing zone, free

The landing zone sits outside DuckLake's catalog and storage, so dlt may use any destination format, write disposition and state handling there.
Two boundaries stay:

- Its own landing prefix, never DuckLake's data path.
- A key that may only write the landing prefix.

A Dagster asset then promotes landing to raw through the IO manager in `append` mode, so lineage reliably starts at raw.
This also covers the push path from [ADR-0002](./0002-api-and-access-strategy.md): an external system drops a file at the API's landing endpoint, which triggers a Dagster job.

### Path 2: into DuckLake, strict

dlt writes through its `ducklake` destination, inside a Dagster asset.
It then writes real DuckLake tables through the catalog: DuckLake tracks every file, the Postgres catalog handles concurrent commits, and maintenance knows which files are in use.
Dagster still sees an asset with a materialization, and there is no landing-to-raw hop.
This makes dlt a second write path next to the IO manager, an explicit exception to ADR-0003, allowed under the rules below.

### Rules for path 2

- **Always inside Dagster.** dlt runs through the official `dagster-dlt` integration (`@dlt_assets`, `DagsterDltResource`), never as a standalone script or cron job.
- **Asset keys follow [ADR-0004](./0004-data-organization-and-ingestion.md).** A custom `DagsterDltTranslator` maps each dlt resource to the layer key (`["raw", <table>]`), and the pipeline's `dataset_name` is the layer (or `<project>_raw` in a shared tenant catalog), so the asset key and the real table name match.
  The OpenLineage bridge names datasets from the asset key, so with dagster-dlt's default key (`dlt_<source>_<resource>`) Marquez would show a table that doesn't exist.
- **Lineage metadata.** dagster-dlt sets `dagster/column_schema` by default, which is enough for the bridge to emit a dataset.
  Chaining `.fetch_row_count()` on the run adds `dagster/row_count` as well.
- **Raw stays append-only.** Only dlt's `append` write disposition into raw (ADR-0004).
  `replace` and `merge` belong to curated layers.
- **Bookkeeping out of the read path.** dlt's `_dlt_loads`, `_dlt_version` and `_dlt_pipeline_state` become DuckLake tables.
  Ideally they go into a dedicated schema, and whether dlt allows that is still to verify (see Enforcement).
- **Config through srdp settings.** dlt gets the catalog connection and the S3 writer key from the same settings classes as the rest (`DuckLakeSettings`, `S3StorageSettings`), not from a separate `.dlt/secrets.toml`.
- **Least privilege.** Only the code location and its run pods hold the writer key, as for the IO manager.

### Incremental state: one owner (open)

Dagster tracks what's processed through partitions, and dlt through its own cursors in `_dlt_pipeline_state`.
If both track the same data, they can disagree about what's already loaded.
One has to own it:

- dlt owns it, and the Dagster asset just calls `pipeline.run()` (simplest, fits API sources with cursors).
- Dagster's partitions own it, and dlt runs per partition without its incremental logic (fits time-partitioned sources and backfills).

To decide before the first real dlt source, possibly per source type.

### Enforcement

The rules hold because the platform ships them as code, not because every project remembers them.

| Rule | Enforced by |
|:---|:---|
| Inside Dagster | `srdp.ingest.dlt` exposes the only supported way to build a dlt asset, a thin wrapper around `@dlt_assets`. |
| Asset keys follow ADR-0004 | The wrapper always uses `SrdpDltTranslator`, which derives the key from the project and layer. A definitions-level test fails if any dlt asset uses another translator. |
| `dataset_name` matches the key | The wrapper builds the dlt pipeline itself and sets `dataset_name` from the same layer, so the two can't drift. |
| Lineage metadata | The wrapper always chains `.fetch_row_count()`. |
| Raw append-only | The wrapper raises before running if a raw resource has a `write_disposition` other than `append`. |
| Bookkeeping out of the read path | To verify: dlt normally keeps `_dlt_*` tables in the same dataset. If they can't be separated, they're hidden from the read path instead. |
| Config through srdp settings | The wrapper builds the `ducklake` destination from `DuckLakeSettings` and `S3StorageSettings`, so there is no `.dlt/secrets.toml` to fill in. |
| Least privilege | Deployment config: only the code location and its run pods get the writer key, the same as for the IO manager. |
| No `filesystem` destination on the data path | The wrapper only offers the `ducklake` destination. Anything else is a review point. |

What code can't catch (a standalone dlt script, for example) is a review point, listed in the PR template's checklist.

### Don't

- Point dlt's `filesystem` destination at DuckLake's data path.
  It writes Parquet files behind the catalog's back and shares DuckLake's directories, so a `replace` load deletes DuckLake's files.
- Run a dlt pipeline outside a Dagster asset against DuckLake's catalog or data path.
- Use dlt's `replace` or `merge` to correct raw data.

### Consequences

- Good, because every dlt load is a Dagster asset with lineage, and its tables are real DuckLake tables that maintenance and time travel cover.
- Good, because dlt's connectors and incremental loading stay available without an extra hop.
- Bad, because there are two write paths into the catalog (the IO manager and dlt), each needing the naming and append-only rules enforced.
- Bad, because dlt's `ducklake` destination is fairly new (dlt 1.30), so a small trial asset should prove it before this ADR is accepted.

## More Information

- [ADR-0001](./0001-platform-architecture-and-distribution.md) (projects define dlt pipelines registered as Dagster assets), ADR-0002 (the API's landing-zone drops), ADR-0003 (single write path, lineage), ADR-0004 (layers, raw append-only, write modes).
- Checked in the `dagster-dlt` source (0.29.25, with dlt 1.30.0 and Dagster 1.13.25): `resource.py` sets `TableMetadataSet(column_schema=..., table_name=..., storage_kind=...)` on every materialization, `dlt_event_iterator.py` adds `row_count` only through `.fetch_row_count()`, and `translator.py` defaults the asset key to `dlt_<source>_<resource>`.
- `src/srdp/lineage/openlineage_bridge.py` emits a dataset when `dagster/row_count` or `dagster/column_schema` is present, named from the asset key through `asset_key_path_to_table_ref`.
- Issue #83, and the review of #75 (`dlt_filesystem_config()` pointed at the data path).
