---
status: proposed
date: 2026-10-02
decision-makers: Yannick Vinkesteijn
---

# dlt alongside Dagster

## Context and Problem Statement

Dagster and dlt do two different but overlapping jobs.
Dagster orchestrates, which means it decides when assets run and in which order, retries them, and shows their history and lineage.
dlt extracts and loads data, with connectors for APIs, databases and SaaS tools, and it handles pagination, authentication, schema evolution, nested data and incremental loading.
Without dlt, each Dagster asset that pulls from an external source implements those parts itself.
For a file loaded once, or one simple extraction, a plain Dagster asset is simpler.

dlt can also write to a destination on its own, outside Dagster and outside DuckLake's catalog.
Some ways of combining the tools lose lineage, and one deletes DuckLake's own files.

How should SRDP support projects that use dlt, so that the platform stays robust while projects keep their own naming?

This ADR covers dlt.
All other Dagster assets write through the IO manager ([ADR-0003](./0003-data-catalog-lineage-and-observability.md)).
dlt isn't a standard SRDP component, so a project chooses whether to use it, and SRDP supports it as described here when it does.

## Considered Options

1. Prescribe one way, where SRDP defines how dlt must be used and allows no exceptions.
2. Secure by default, where SRDP provides one way with its rules switched on, and a project relaxes a rule only explicitly in its config.
3. No guidance, where projects find out for themselves.

## Decision Outcome

Chosen option: "Secure by default", because high-risk users such as hospitals need a platform whose defaults meet their security baseline, and every project still keeps its own naming.
SRDP is meant to serve as the data platform infrastructure and backend for many projects, so it needs to be robust by default and permissive only where a project explicitly chooses so.
Each relaxation is a visible line in `srdp.toml` (#42), so an auditor can see exactly which rules a deployment has switched off.
Prescribing one way without exceptions would force existing setups into a migration, and no guidance leaves every project to find the failures below on its own.

Dagster is the orchestrator, so dlt is only used as a library inside Dagster assets, and dlt's own scheduling and runners aren't used.
By default dlt goes one of two ways, both inside a Dagster asset through `dagster-dlt`.

1. **Into the landing zone, as quarantine.**
   dlt drops the data in the landing zone, outside DuckLake, and a Dagster asset validates it and promotes it to raw through the IO manager.
   This suits irregular or untrusted input.
   dlt still normalizes the data and keeps its incremental state with it in the landing zone, while the IO manager stays the only writer into DuckLake.
   The landing zone's retention keeps dlt's state files, so a cleanup doesn't trigger a full reload.
2. **Into raw, through the strict setup.**
   dlt writes through SRDP's helper straight into raw in the shared DuckLake catalog, with the rules below switched on.
   This suits sources with a known structure.

SRDP follows the medallion layering of [ADR-0004](./0004-data-organization-and-ingestion.md), so dlt only writes into the landing zone or raw (bronze).
SRDP keeps the transformations into later layers in dbt and Dagster assets, so dlt doesn't write there.
In both ways the helper records the real table name on the asset, so lineage in Dagster and Marquez follows the project's own names.

A project that wants something else switches it off explicitly in `srdp.toml`, and the helper logs a warning on every load that runs with a rule switched off.

Naming stays the project's choice, within the three levels DuckLake and DuckDB support (catalog, schema, table).
A guide on building pipelines (planned) describes the remaining choices, such as nested data, and what each one costs.

### Data leaving SRDP

dlt is used for ingestion only.
Data leaves SRDP through the API, which [ADR-0005](./0005-authorization-and-data-access.md) makes the policy enforcement point, with data contracts and consumer surfaces from [ADR-0008](./0008-identity-propagation-and-data-contracts.md).
An export with dlt would duplicate that path while bypassing its authorization, data contracts and audit trail, so SRDP doesn't offer one.

### What breaks

These follow from how DuckLake and dlt work, whatever a project chooses otherwise.

- dlt's `filesystem` destination pointed at DuckLake's data path breaks DuckLake.
  It writes files there without the catalog knowing, so a `replace` load deletes files DuckLake relies on, and DuckLake's orphan cleanup deletes dlt's files in turn.
- dlt's `ducklake` destination with different catalog settings writes into a separate catalog in the same database.
  dlt passes its own metadata schema (`ducklake`) by default, while SRDP uses `public` implicitly, so the rest of SRDP doesn't attach dlt's catalog and its tables stay invisible.

The helper avoids both.

### What SRDP will provide

None of this exists yet, and each part gets its own issue.

- SRDP defines its catalogs explicitly in one place through configuration, each with a name, a metadata schema and a data path.
  Every component that attaches DuckLake (the IO manager, the API, the apps, dbt and the dlt helper) reads that definition, so they all see the same catalog.
  Today no component sets a metadata schema, so DuckLake falls back to `public`, and the first definition either keeps that value or moves existing lakes to a named schema in a one-off migration.
  [ADR-0006](./0006-deployment-and-project-isolation-model.md)'s shared tenant catalogs and per-project catalogs become entries in the same definition.
- The catalog definition names which writer owns each schema, for example dlt for raw and dbt for curated.
  The dlt helper only writes into schemas owned by dlt and the IO manager stays out of them, so a mix-up fails before loading instead of during it.
  This matters because dlt can't load into a table another writer created (it adds its own columns with a constraint DuckLake refuses), and mixing writers in one table goes against Dagster's model of one asset per data object anyway.
- SRDP provides a helper in `srdp.ingest.dlt`, a thin layer over `dagster-dlt`.
  It builds the `ducklake` destination from the catalog definition, so dlt writes into the catalog the rest of SRDP reads.
- Lineage follows the project's own names.
  `dagster-dlt` doesn't record the physical table on its own, so the helper sets the fully qualified table name in the asset's definition metadata.
  The helper also drops the extra upstream dependency `dagster-dlt` adds by default, which would otherwise show up in Marquez as an input that doesn't exist.
  SRDP's lineage bridge and IO manager read that name, and fall back to the asset key only when it isn't set.
- In a shared tenant catalog, the helper derives the dataset name from the project and layer (`<project>_raw`), as ADR-0006 does for schemas, and a project can override it.
- In a deployment, only the code location holds the key that can write data.
  Apps, SQL consoles and other UIs stay read-only by default, as [ADR-0005](./0005-authorization-and-data-access.md) decides, and write access through a UI is only granted explicitly through the writer role.
  Today DuckLake connects as the Postgres superuser, so this is a change in itself.
  In development the same model applies, with keys generated locally (#69).

### Rules on by default

The helper applies these rules unless a project switches one off in `srdp.toml`.

| Rule | Why | Cost of switching it off |
|:---|:---|:---|
| Raw is append-only (dlt's `append` write disposition), as [ADR-0004](./0004-data-organization-and-ingestion.md) decides | Raw keeps a complete audit trail, and curated layers can always be rebuilt from it. | The project loses the audit trail and the rebuild from raw for those tables, and dropping a table also resets dlt's incremental state. |
| Raw is validated before loading (dlt's schema contract freezes data types while new columns are still allowed, and Dagster asset checks cover content), as ADR-0004 requires when skipping the landing zone | Unconforming data is caught before it reaches the lake, while sources can still add fields. | A type mismatch silently adds a variant column (such as `amount__v_text`) instead of failing. |
| Each schema has one writer, from the catalog definition, and dlt only writes into the landing zone or raw | dlt can't load into tables another writer created, and Dagster models one asset per data object. | A mix-up fails during the load instead of before it. |

Switching a rule off is one explicit line in `srdp.toml`, so it shows up in review and in an audit, and the helper warns on every load that runs without it.

### Consequences

- Good, because a fresh deployment meets a strict baseline out of the box, and every relaxation is visible and auditable in one file.
- Good, because projects keep their own naming, and an existing setup relaxes the rules it needs instead of migrating.
- Bad, because a project with loose pipelines has to switch rules off explicitly, or adapt.
- Bad, because the IO manager and dlt are two write paths into the catalog, which only see each other when their catalog settings match.
- Bad, because dlt's `ducklake` destination is fairly young (added in dlt 1.17), so a trial source should prove it before this ADR is accepted.

## More Information

- [ADR-0001](./0001-platform-architecture-and-distribution.md) (projects define dlt pipelines as Dagster assets), ADR-0002 (writes through the API or Dagster), ADR-0003 (write path, lineage), [ADR-0004](./0004-data-organization-and-ingestion.md) (layers, landing zone, raw append-only), ADR-0006 (shared tenant catalogs, metadata schemas).
- This ADR revises ADR-0002, ADR-0003 and ADR-0004, which carry a note pointing here.
- The `filesystem` failure, the metadata schema mismatch, and `dagster-dlt`'s `dagster/table_name` (which uses the source name as schema) were checked in local tests against dlt 1.30.0, `dagster-dlt` 0.29.25, Dagster 1.13.25 and DuckDB 1.5.2.
- Issue #83, and the review of #75.
