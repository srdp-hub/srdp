---
applyTo:
  - "projects/**/assets/**/*.py"
  - "projects/**/definitions.py"
  - "src/srdp/assets/**/*.py"
  - "src/srdp/io/**/*.py"
  - "src/srdp/resources/**/*.py"
---

## Dagster conventions

### Runtime architecture

Dagster runs as a multi-process server, not a single script:

- **Webserver**: serves the UI and GraphQL API (stateless, horizontally scalable).
- **Daemon**: runs schedules, sensors, and run queue (single instance).
- **User code server**: gRPC process loading your definitions (can be multiple per project).

PostgreSQL is the stateful backend. It stores run history, event logs, schedules, and asset metadata. Treat it as critical state: back it up, never delete its PVC without understanding the consequences.

### Assets

- Use typed return annotations: `Output[pl.DataFrame]`, `Output[str]`.
- IO manager key for DuckLake persistence: `io_manager_key="ducklake"`.
- `defs = Definitions(...)` is the only top-level export in `definitions.py`.

### K8s resource profiles

Import from `srdp.resources.k8s` — never redefine inline:

- `BASE_RUN_K8S_CONFIG` — standard workloads
- `FAST_LANE_K8S_CONFIG` — latency-sensitive
- `BACKFILL_K8S_CONFIG` — batch processing

### Priority scheme

| Lane | Priority |
|:---|:---|
| fast-lane | 5 |
| default | 0 |
| background | −1 |
| backfill | −2 |

### Project structure

- Client assets live in `projects/<name>/src/etl/assets/`.
- The module is loaded via `-m etl.definitions`.
- To add a new client: copy `projects/cbs-example/` and rename.
- Reusable platform code belongs in `src/srdp/`, not in a project.
