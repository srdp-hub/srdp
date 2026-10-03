---
title: Architectural Decision Records
icon: lucide/landmark
---

# Architectural Decision Records

Decisions that shape the platform's architecture, recorded using the [MADR](https://adr.github.io/madr/) format. See [Contributing](../07-contributing.md#architectural-decision-records-adrs) for when and how to write an ADR.

| ADR | Status | Decision |
|:---|:---|:---|
| [0001: Platform architecture and distribution](./0001-platform-architecture-and-distribution.md) | Accepted | Single package with extras, base images for client deployment, four platform layers, semver over an explicit public API |
| [0002: API and access strategy](./0002-api-and-access-strategy.md) | Accepted | Traefik + Zitadel at the edge, FastAPI as unified API, zero-trust internal model with a read/write split, audit logs on blob |
| [0003: Data catalog, lineage, and observability](./0003-data-catalog-lineage-and-observability.md) | Accepted | DuckLake as mandatory IO wrapper, OpenLineage core with a pluggable backend (default PostgreSQL; optional Marquez), layered observability, failure-mode matrix and drift detection |
| [0004: Data organization and ingestion](./0004-data-organization-and-ingestion.md) | Accepted | Configurable medallion layers (landing/raw/curated), raw append-only invariant, governed erasure, landing-zone-first ingestion |
| [0005: Authorization and data access](./0005-authorization-and-data-access.md) | Accepted | API as the policy enforcement point, project-scoped RBAC, Zitadel for identity and grants, read/write split, exposed Dagster UI is read-only |
| [0006: Deployment and project isolation model](./0006-deployment-and-project-isolation-model.md) | Accepted | Deployment is the isolation unit; catalogs default per tenant (tenant/project_layer/entity), with a dedicated catalog available per project (project/layer/entity) |
| [0007: Compute, execution, and scaling](./0007-compute-and-scaling.md) | Accepted | In-process executor (k8s optional), tiered read paths, hybrid serve-vs-offload with async jobs, writes via Dagster |
| [0008: Identity propagation, capability tokens, and data contracts](./0008-identity-propagation-and-data-contracts.md) | Accepted | Validated JWTs at every hop, PEP-issued short-lived capability tokens, data contracts as the fine-grained scope artifact, constrained evaluator, OData consumer endpoint |
| [0009: Data-plane access: credential materialization](./0009-data-plane-credential-materialization.md) | Accepted | Capability token → scoped engine/storage/compute credentials; credentials subordinate to the token, fine-grained scope mediated-only, compute as a distinct capability |
| [0011: Project onboarding and extension](./0011-project-onboarding-and-extension.md) | Proposed | `srdp.toml` + `srdp-project.toml`, TOML, local-path-or-repo-reference project discovery, services vs. code locations as separate manifest entries, config-only interface for v1 |
| [0012: dlt alongside Dagster](./0012-dlt-alongside-dagster.md) | Proposed | Secure by default: Dagster orchestrates, dlt runs as a library inside its assets, either into the landing zone as quarantine or straight into raw through a strict SRDP helper, never beyond raw; dlt only ingests, data leaves through the API; relaxing a rule is explicit in `srdp.toml` and warns on every load; naming stays free |
