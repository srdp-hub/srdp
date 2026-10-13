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

Dagster runs as three kinds of processes.

- The webserver serves the UI and the GraphQL API. It is stateless and can scale out.
- The daemon runs schedules, sensors and the run queue. Run only one daemon.
- A code server is a gRPC process that loads the definitions. A project can have several.

PostgreSQL stores run history, event logs, schedules and asset metadata.
Back it up. Never delete its volume or PVC.

### Assets

- Annotate return types, such as `Output[pl.DataFrame]` or `Output[str]`.
- Register `ducklake_io_manager` under the `"io_manager"` key, so every asset uses it by default.
- Give an asset that cannot return a `pl.DataFrame` or `pl.LazyFrame` its own `io_manager_key`.
- Export only `defs = Definitions(...)` from `definitions.py`.

### K8s resource profiles

Import these profiles from `srdp.resources.k8s`. Do not define them again.

| Profile | Use |
|:---|:---|
| `BASE_RUN_K8S_CONFIG` | Standard workloads |
| `FAST_LANE_K8S_CONFIG` | Latency-sensitive runs |
| `BACKFILL_K8S_CONFIG` | Batch processing |

### Priority scheme

| Lane | Priority |
|:---|:---|
| fast-lane | 5 |
| default | 0 |
| background | −1 |
| backfill | −2 |

### Project structure

- Put client assets in `projects/<name>/src/etl/assets/`.
- Load the module with `-m etl.definitions`.
- To add a client, copy `projects/cbs-example/` and rename it.
- Put reusable platform code in `src/srdp/`.
