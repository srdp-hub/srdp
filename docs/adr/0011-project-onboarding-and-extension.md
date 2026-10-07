---
status: proposed
date: 2026-09-27
decision-makers: Yannick Vinkesteijn
---

# Project onboarding and extension

## Context and Problem Statement

A deployment starts as the standard SRDP platform (Zitadel, Traefik, Postgres, Dagster core, the identity/routing/orchestration skeleton) and nothing project-specific.
Turning it into an actual working platform means filling it with a project's own pipelines and platform surface.
ADR-0006 settled the data model for that (a project is a bundle of any number of code locations, services, and endpoints, catalogs default per tenant).
It did not settle the operational question: how does a project's code and configuration actually get registered into a running deployment, and how does a project add its own service or API endpoint behind the platform's existing SSO gate.

Today there is no answer beyond hand-editing `docker-compose.yml`/`config/traefik/traefik.yml` directly.
This has real, current consumers, grounded in hands-on experience.
Two open issues describe exactly this gap from hands-on experience:

- **#54**, "A contract for registering a client app behind the SSO gate": adding a real client dashboard (datavloot's Crows Nest) to the Compose stack required hand-writing Traefik labels against SRDP internals, overriding the entire `oauth2-proxy` command to add one OIDC scope, and overriding `authResponseHeaders` because the access token is withheld by default.
  Its own proposed solution: "a stanza in `srdp.toml` (#42) that renders the labels", `scopes` as configuration instead of a full command override, and the access token forwarded by default.
- **#55**, "`srdp.auth`: shared implementation of ADR-0008 token validation": ADR-0008 requires every client app to validate its own access token, no shared implementation ships, so each app re-derives it (~280 lines in the same dashboard: JWKS fetch and caching, JWT verification, Zitadel role-claim parsing, userinfo fallback for opaque tokens).
  Its own proposed solution: `srdp.auth` as the `srdp[auth]` extra, with `validate_token(token) -> claims`, `roles_for(claims) -> set[str]`, and an ASGI middleware for Starlette/FastAPI.

Datavloot's own Valk integration separately hits the same underlying gap from a different angle: it works by git-cloning SRDP at a pinned commit and hand-diffing `deploy/docker/` on every version bump, because there is no stable, versioned contract to render against instead.
The platform's own service-onboarding boilerplate (a Traefik router/service pair repeated per gated service, `config/dagster/workspace.yaml` hardcoding a single code location) is hand-copied the same way internally.

**The end goal**: a flexible connection between SRDP's infrastructure and the projects, services, and platform implementations built on it.
The same schema and mechanism generate configuration for both deploy targets, Docker Compose and Kubernetes, so a project's manifest stays deploy-target-agnostic.

**Explicitly out of scope**: sharing platform infrastructure between multiple distinct tenants (one shared Zitadel/Traefik/Dagster/Postgres serving several separate tenant identities).
ADR-0006 already treats that as the deployment owner's optional choice, already distinct from the default, and nothing here needs to design for it.
Single-tenant-per-deployment is the assumed shape throughout this ADR.

## Considered Options

1. Formalize the git-clone-and-diff pattern with better documentation, no config schema.
   Cheapest, but does not add the capability a stable contract would provide, every SRDP refactor still requires downstream consumers to re-diff internals by hand.
2. A published, versioned config schema (`srdp.toml`, already proposed in #42) that a project renders against, extended to also cover project/service onboarding, beyond internal operator config.
3. A live API/UI for adding services and code locations at runtime, no static config.

## Decision Outcome

Chosen option: extend #42's `srdp.toml` to also serve external project onboarding, beyond internal operator config, config-only for v1 (rejecting option 3, see below).

### Two manifests

- **`srdp.toml`**: the platform/infra definition.
  Domain, topology, and a list of project entries.
  Lives in exactly one place per deployment, a platform/infra repo belonging to no single project.
  A project repo owning `srdp.toml` collapses back to one-project-per-infra, the same coupling this ADR exists to remove.
- **`srdp-project.toml`**: a single project's manifest, its full bundle of code locations, services, and endpoints.
  Lives in the project's own repo.
  `srdp.toml`'s project entries can be either a local path (single-repo deployments, the file is already checked out) or a repo reference (`{name, repo, ref, path}`, the tooling fetches it first).
  No format fork between the two, local path is the degenerate case of a repo reference.

Both use TOML: consistent with `pyproject.toml`/ruff config already in this repo, stricter types than YAML for something hand-edited (no implicit-boolean footguns), and the data here is shallow enough that TOML's array-of-tables stays readable.
`tomllib` (stdlib, Python 3.11+) covers reading.
Writing back with comments preserved (`srdp env add`-style commands) specifically needs `tomlkit`, the only one of the two libraries that supports it.

### A project's bundle is open-ended across resource types

Earlier drafts of this ADR defined exactly two manifest entry types, `[[services]]` and `[[code_locations]]`.
That is too narrow.
A project's bundle spans at minimum:

- **`[[code_locations]]`**: a Dagster gRPC server, consumed only by Dagster, no Traefik route.
  `config/dagster/workspace.yaml` hardcodes a single `grpc_server` entry today even though Dagster's own `load_from:` accepts a list natively, the gap is entirely on SRDP's side.
- **`[[services]]`**: a container needing a real Traefik route, optionally gated behind `zitadel-auth` (the concrete case: datavloot's Crows Nest, #54).
  `config/traefik/traefik.yml` repeats the same router/service shape per gated service today, hand-written each time.
- **`[[endpoints]]`**: API routes a project registers against SRDP's own FastAPI surface, rather than running its own separate service.
  Directly resolves #54's proposal to make `scopes` and `authResponseHeaders` (specifically, forwarding the access token by default rather than requiring every client to override the command) configuration instead of a full `oauth2-proxy` command override.
- **`[[connections]]`**: storage or external database credentials a project needs (an S3 bucket, an external Postgres).
  Scope and shape not yet designed, flagged here so the manifest format is not closed off against it later.

Each entry type lets the tooling generate its own boilerplate (Traefik config, `workspace.yaml`, FastAPI router registration) instead of it being hand-written.
A project declares any combination, any count of each, or none.

### Token validation ships as a library: `srdp.auth`

Per #55: every client app must validate its own access token per ADR-0008 (JWKS fetch and caching, JWT verification, Zitadel role-claim parsing, userinfo fallback for opaque tokens), and nothing ships that does this today, `src/srdp/api/main.py`'s own docstring says as much ("without the capability-token/JWT validation machinery ADR-0002/0008 describe for a production deployment").
`srdp[auth]` (the extra), implementing #55's proposed surface (`validate_token`, `roles_for`, an ASGI middleware), is what a project's `[[services]]`/`[[endpoints]]` entries would actually import to satisfy ADR-0008, rather than each project re-deriving it independently the way the reference client already had to.

### Code-location and service registration: a per-project config file

Three mechanisms considered, any combination is possible: a Python client call (`srdp.register_code_location(...)`, imperative, has an import-time side effect), pure directory convention (auto-discover `projects/<name>/`, no explicit registration, but not self-documenting), or a small manifest per project (`srdp-project.toml` itself, declarative and explicit without the import-time side effect).
The third is the leading candidate, it is what `srdp-project.toml` already is, no separate mechanism needed.

**Validation**: a pydantic model for schema validation (free, same pattern as `DuckLakeSettings`), plus a separate path-resolution pass (does a declared code-location module actually import, do referenced files exist) that pydantic alone cannot do.
Belongs in an `srdp validate` step run before `workspace.yaml`/compose generation, so a bad manifest fails fast with a clear error instead of surfacing later as an opaque Dagster gRPC failure.

### Container sourcing: reuse Compose's own image/build duality

Each service/code-location entry carries either `image:` or `build:`, exactly like Compose already allows.
`srdp deploy --dev` resolves this per environment: local dev defaults to building SRDP's own services and *pulling* a project's published image (never assume an external project's build toolchain is set up locally), a production-style target defaults the other way for SRDP's own services.

**Confirmed gap**: the Kubernetes chart pulls the platform images from `ghcr.io/srdp-hub` and the example project's images from `rg.nl-ams.scw.cloud/srdp-registry/...`, built and pushed by `deploy/opentofu/scaleway/build-and-push.sh`.
Compose has no equivalent, `docker-compose.prod.yml` has no `image:` override at all, and `deploy/opentofu/gcp/startup-script.sh.tpl` clones the repo and builds on the target VM (`git clone` + `docker compose up --build`).
Both predate this ADR and are not treated as fixed points, patch or replace them to reach the stated default, whichever gets there.
Plan: publish SRDP's own service images to a real registry on release (`ghcr.io`, reusing the GitHub Actions release pipeline already built for PyPI), have `docker-compose.prod.yml` pull from it, and point the GCP startup script at that instead of building from a git clone.
Not built yet.

### Interface: config-only, one-shot `srdp deploy`

Adding a service or code location always needs a new container to exist and start, no interface avoids that, and Dagster's own workspace reload only works against a code server already running.
The real mechanism is inherently: write the manifest, regenerate `workspace.yaml`/Traefik config, `docker compose up -d` or `helm upgrade` (idempotent), reload.
No live reconciliation controller for v1, on Kubernetes or Compose.
A UI-driven "add service" affordance can be built later as a thin layer over the same path, config-first now leaves that fully open.

**Invariant that comes with choosing one-shot deploy over live reconciliation**: `srdp deploy` must update an already-running deployment safely, without data loss, covering both initial creation and every update after it.
Changing a project's code location image, adding a service, or bumping the platform itself are all updates applied in place to an existing stack.
Helm's `helm upgrade` and Compose's `docker compose up -d` both already give this property for unchanged resources (stateful volumes, existing catalogs, Postgres data survive a redeploy by default); this needs to be a stated, deliberately preserved property of `srdp deploy`/`srdp validate`, stated explicitly rather than left implicit in what the underlying tools happen to do.

### Design principle: sensible defaults, full override

`srdp.toml`/`srdp-project.toml` follow one rule throughout: every dimension has a default that lets a base deployment work with zero configuration, and every dimension is explicitly overridable for exact control.
Pulled together entirely from decisions already made tonight:

| Dimension | Default (zero-config base) | Overridable to |
|:---|:---|:---|
| Catalog scope | one shared catalog per tenant (ADR-0006) | a dedicated catalog per project |
| Domain | `*.srdp.localhost` (local dev) | any real domain |
| TLS source | mkcert (local dev) | Let's Encrypt / ACME |
| Container sourcing | build for SRDP's own services, pull for a project's published image | either, per entry, via `image:`/`build:` |
| Tenancy | single-tenant per deployment (ADR-0006) | multi-tenant, an undesigned future choice for the owner |
| Storage backend | local filesystem (the only one that exists today, `src/srdp/io/storage.py`) | S3/Azure, once built, tracked separately |
| Gating (`[[services]]`/`[[endpoints]]`) | behind `zitadel-auth` | ungated, explicit opt-out per entry |

The base deployment this produces: one tenant, one shared catalog, whatever code locations and services a project's manifest declares, the standard platform applications (Zitadel, Traefik, Dagster core) with their own base settings, nothing project-specific assumed beyond what's declared.
Two entries in this table are genuinely undecided rather than just unbuilt, storage backends beyond local filesystem, and what multi-tenant's actual configuration surface looks like once someone needs it, both flagged rather than guessed at here.

### Setup-service scope widens

The already-scoped setup service (closes #35, OIDC credential bootstrap) also walks every declared service, endpoint, and code location and confirms each is actually reachable (a health check, or Dagster gRPC connectivity) before exiting.
Full stack verification, beyond confirming containers started.
Same `depends_on: condition: service_completed_successfully` pattern it already uses for Zitadel's own init step.

## Consequences

- Good, because a project stops needing to know SRDP's internal service/network names to add itself to a deployment.
- Good, because the same schema serves single-repo and multi-repo deployments, no fork to maintain.
- Good, because #54 and #55 already describe two of the resource types (`[[services]]`/`[[endpoints]]` registration, and `srdp.auth`) from hands-on experience, this ADR builds on that instead of re-deriving it.
- Good, because container sourcing and service onboarding both reuse patterns (Compose's `image`/`build`, Traefik's existing router shape) already proven in this repo, rather than inventing new mechanisms.
- Good, because the safe-update invariant is stated explicitly rather than left to whatever the underlying deploy tool happens to do by default.
- Bad, because this is real, multi-session design and build work, `srdp.toml` itself (#42) and the extension-point contract have no prior implementation to build from.
- Bad, because a per-project manifest is one more file for a project to maintain and keep correct, mitigated by the validation step catching mistakes early.
- Bad, because the `[[connections]]` resource type (storage/external database credentials) is identified but not designed, a known gap left for follow-up rather than blocking this ADR.

## Pros and Cons of the Options

### Formalize git-clone-and-diff with better docs only

- Good, because it costs nothing to build.
- Bad, because it does not add the capability a stable contract would provide, a project like Valk still hand-diffs `deploy/docker/` on every SRDP version bump, documentation alone does not change that.

### Live API/UI for runtime service addition, no static config

- Good, because it would feel more like a real platform product.
- Bad, because it does not remove the actual constraint, a new container still has to exist and start before anything can route to it, so the API would just be a thin wrapper writing the same config underneath.
  Building the API before the config exists gets the layering backwards.

## Related

#42 (`srdp.toml` proposal, extended here), #54 (SSO-registration contract, folded into
`[[services]]`/`[[endpoints]]`), #55 (`srdp.auth`, the token-validation library this ADR's resource types depend on), #35 (OIDC bootstrap, the setup service this ADR widens), #23 (Dagster code-location onboarding docs, related but narrower than `[[code_locations]]`'s registration mechanism).
