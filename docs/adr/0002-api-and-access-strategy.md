---
status: accepted
date: 2026-06-08
revised: 2026-10-03
decision-makers: Yannick Vinkesteijn
---

# API and access strategy

> **Proposed revision** in [ADR-0012](./0012-dlt-alongside-dagster.md) (#84): writers other than the API and Dagster are only permitted explicitly.

## Context and Problem Statement

SRDP runs multiple backend services (Dagster, DuckLake, PostgreSQL) and optional user-facing tools (Dagster UI, marimo, quarto). Without a unified access strategy, external consumers would need to know which internal tool to call for each operation, and each service would need its own authentication, logging, and API contract.

This ADR addresses how traffic flows through the platform at three communication levels, and decides the questions an earlier draft left open: the internal trust model, where writes happen, and how access is logged.

1. External: how do external consumers access the core platform programmatically? (consumer → Traefik → FastAPI)
2. External through service: how do users access optional services that themselves interact with platform backends? (user → Traefik → service → platform backends)
3. Internal: how do services communicate with each other and with platform backends over the shared network? (service → service/backend)

## Considered Options

### Ingress and authentication

1. Traefik + Zitadel + oauth2-proxy forwardauth: one ingress, one identity provider, one auth middleware for all external access.
2. nginx + Keycloak: similar pattern with nginx as reverse proxy and Keycloak as identity provider.
3. Per-service OIDC: each service registers its own OIDC client and handles login independently.

### Core platform API framework

1. FastAPI (REST + OpenAPI): async-first Python web framework with native Pydantic integration and automatic OpenAPI generation.
2. gRPC: a high-performance RPC framework, primarily for inter-service communication.
3. Expose backends directly: let consumers call Dagster GraphQL and DuckDB themselves.

### Internal trust model

1. Network boundary as trust boundary: services on the closed network are implicitly trusted, no per-service authentication.
2. Zero-trust: authenticate by who is making the request at every service, regardless of network position.

## Decision Outcome

### Communication levels

All external traffic enters through Traefik, which terminates TLS and enforces authentication via Zitadel + oauth2-proxy forwardauth. Behind Traefik, traffic reaches the platform through three distinct levels:

```
                               ┌─── Dagster UI (optional) ──┐
                               ├─── marimo (optional)       │
External traffic → Traefik ────┤                            │
                   (Zitadel)   ├─── quarto (optional)       │
                               │                            ▼
                               └─── FastAPI ──┬── DuckLake (read-only)
                                              ├── Platform management
                                              └── Dagster GraphQL ← ──┘
                                                     ↑
                                              (internal only)
```

Level 1, external: programmatic consumers (dashboards, CLI tools, third-party integrations) access the core platform through FastAPI. FastAPI composes results from multiple backend services (DuckLake, Dagster GraphQL, platform config) into a unified, function-oriented API. Consumers interact with capabilities (query data, trigger a job, check health), not with individual tools. Internal backends like Dagster GraphQL are never exposed directly.

Level 2, external through service: users access optional services (Dagster UI, marimo, quarto) in a browser, routed directly from Traefik. These services then interact with platform backends internally. Each service is registered with Traefik labels and the forwardauth middleware, so the platform knows who is using it. No service implements its own login flow. The platform functions without any of these deployed.

Level 3, internal: services communicate with each other and with platform backends over the shared container network, under the zero-trust model decided below.

### Ingress and authentication: Traefik + Zitadel + oauth2-proxy

Chosen option: "Traefik + Zitadel + oauth2-proxy forwardauth", because it enforces authentication at the edge without touching service code, integrates natively with both Docker Compose (labels) and Kubernetes (IngressRoute CRDs), and Zitadel provides a self-hostable identity stack.

- Unauthenticated requests are redirected to `/oauth2/sign_in`.
- Authenticated requests carry `X-Auth-Request-*` headers as display hints only; services derive identity by validating the forwarded Zitadel JWT, not from these headers (see [ADR-0008](./0008-identity-propagation-and-data-contracts.md)).
- Adding a new service requires only the Traefik forwardauth middleware labels.
- TLS certificates are configured once in Traefik's static config, not per service.
- For machine-to-machine access, Zitadel service accounts authenticate via the OAuth2 token endpoint.

Edge authentication establishes identity. It does not decide which data a principal may read or write; that is authorization, handled at the API (see [ADR-0005](./0005-authorization-and-data-access.md)).

### Core platform API: FastAPI

Chosen option: "FastAPI", because the platform's API is a browser- and script-facing, data-serving gateway that composes several backends, which is exactly what an HTTP + OpenAPI (REST) framework with native Pydantic models fits. gRPC is the considered alternative and is deferred (see below); exposing Dagster GraphQL and DuckDB directly would remove the single place where authentication, authorization, and audit are applied.

The API composes data and operations from multiple backends into one surface, organized by function (data access, orchestration, platform management), not by backend tool. It generates an OpenAPI specification directly from Pydantic models, runs as a container behind Traefik, and establishes identity by validating the forwarded Zitadel JWT (see [ADR-0008](./0008-identity-propagation-and-data-contracts.md)), not by trusting forwardauth headers. Each platform module registers its endpoints as a FastAPI router; new modules add a router without modifying shared application code.

The API is stateless and scales horizontally (multiple workers per host, multiple replicas behind Traefik, async I/O for I/O-bound calls). It is not a single-server bottleneck. The bottleneck to manage is in-process query compute, which is the subject of [ADR-0007](./0007-compute-and-scaling.md).

**On gRPC.** gRPC is a ready-made, high-performance RPC framework that wins for high-volume, low-latency, and streaming internal traffic (less so for large payloads). It is not the right fit for the platform API now, for two reasons. First, that API is external and human/script-facing, where REST is more mature in tooling and gRPC is discouraged: gRPC runs over HTTP/2 (not natively supported everywhere), and external parties cannot connect without a generated client or grpc-web. Second, adopting gRPC for the API means giving up the FastAPI layer we get for free (Pydantic validation, OpenAPI/Swagger) and rebuilding it ourselves. The internal high-performance case is already covered by the tool-specific protocols that are optimized for it: Dagster uses gRPC for its own internal comms, so that benefit lives inside Dagster and is not ours to build. The platform therefore leans on Dagster and existing tool protocols internally, and routes everything else through the REST API or an authn/visibility proxy. After v1 we can measure where bottlenecks actually are and, if warranted, apply gRPC to specific hot paths behind the proxy rather than committing to it up front.

### Internal trust: zero-trust, with a read and write split

Chosen option: "Zero-trust". The closed network adds protection, but is not sufficient on its own. Every service still authenticates requests regardless of network position. Interactive services sit on the internal network, but a human drives them through a browser and can touch data, so each is effectively an external surface. The platform authenticates by who is making the request at every service, not by network position.

Because DuckDB and DuckLake have no per-row or per-column security, authorization cannot be pushed into the engine. The platform therefore splits reads from writes:

- Reads: interactive tools (marimo, quarto, ad-hoc analysis) get project-scoped read access. How a grant is materialized into engine and storage credentials, and why fine-grained or sensitive reads are served through a mediated path rather than a direct connection — is decided in [ADR-0009](./0009-data-plane-credential-materialization.md). Project isolation follows the catalog boundary of [ADR-0006](./0006-deployment-and-project-isolation-model.md).
- Writes: all writes to lake-managed layers go through Dagster by default, for lineage, retries, and asset checks (see [ADR-0003](./0003-data-catalog-lineage-and-observability.md), [ADR-0007](./0007-compute-and-scaling.md)). The API's only write role is accepting drops into the landing zone (which trigger Dagster jobs) plus explicitly-defined operational write endpoints. Interactive tools that need to write use these same steps (the API and Dagster path), never a direct writable engine connection. The invariant is that every write is mediated and on-path (lineage, audit, asset checks). In case of interactive tools with write access the same applies.

Service-to-service backend calls that are not user-driven and do not cross a trust boundary may stay network-local on a single-node deployment, which is adequate: the invariant is that the network is never a data-authorization boundary, so any path that carries user identity or touches data must authenticate, while purely internal non-boundary calls need not. When a real boundary appears (multi-node, compliance), they authenticate with Zitadel service accounts (machine-to-machine client credentials), reusing the existing identity stack. mTLS or a service mesh is the heavyweight version and is out of scope.

### Activity logging

The platform records who accessed what, when, and through which path. The decision (storage and granularity) lives with observability in [ADR-0003](./0003-data-catalog-lineage-and-observability.md): access and audit logs are written as append-only structured records (JSONL or Parquet) to blob storage, which survives the outage of any single stateful service.

- API and service access is logged per request at the Traefik and FastAPI layers, including what was requested. All writes are audited because they only occur via the API or Dagster.
  Other writers are only permitted explicitly ([ADR-0003](./0003-data-catalog-lineage-and-observability.md)), and a writer outside the API and Dagster, for example during development, isn't audited this way.
- Direct interactive reads are audited at session and grant level by default (a user received a read-only session on a project at a time). Read-only access removes the mutation risk but not read confidentiality, which is a separate concern; deployments that need per-query read audit escalate by routing reads through the API or a logging wrapper (see the read tiers in [ADR-0007](./0007-compute-and-scaling.md)). The read-audit granularity is a per-deployment policy tied to data sensitivity.

### Consequences

- Good, because authentication is enforced once at the Traefik edge for all external access, both services and API.
- Good, because FastAPI composes multiple backends into a unified API surface, and internal backends like Dagster GraphQL are never exposed directly.
- Good, because the trust model is explicit (zero-trust) and the read and write split removes the "users adjust data off-path" risk by construction.
- Good, because activity logging is decided (append-only on blob) rather than deferred, and the default loses no read performance.
- Good, because automatic OpenAPI docs mean consumers can generate typed clients without manual spec maintenance.
- Bad, because the forwardauth round-trip adds one hop per authenticated request.
- Bad, because authorization must be enforced in the API rather than in the engine, since DuckLake has no native row or column security (see [ADR-0005](./0005-authorization-and-data-access.md)).

## Pros and Cons of the Options

### nginx + Keycloak

- Good, because nginx is widely known.
- Bad, because Keycloak is heavyweight and resource-intensive for a self-hosted single-deployment setup.
- Bad, because nginx does not have the same native Docker and Kubernetes label-driven config model as Traefik.

### Per-service OIDC

- Good, because each service is independently deployable.
- Bad, because each service must maintain its own OIDC client, session handling, and token refresh logic.
- Bad, because inconsistent auth UX across services.

### gRPC

- Good, because it is a ready-made framework (not DIY) and wins on high-volume, low-latency, and streaming traffic.
- Good, because the internal high-performance case is already covered: Dagster uses gRPC for its own internal comms, at no build cost to us.
- Bad, because it runs over HTTP/2, which is not natively supported everywhere, and external/human-facing consumers need a generated client or grpc-web to connect.
- Bad, because adopting it for the platform API forfeits the free FastAPI layer (Pydantic validation, OpenAPI/Swagger), which we would have to replace ourselves.

### Expose backends directly

- Good, because no gateway to build or run.
- Bad, because it removes the single choke point for authentication, authorization, and audit, which the zero-trust model depends on.

### Network boundary as trust boundary

- Good, because it requires no internal authentication.
- Bad, because interactive tools are browser-driven external surfaces, so a closed network does not actually bound who can reach data.
- Bad, because it makes the network perimeter a single point of compromise.
