# AGENTS.md: SRDP Codebase Guide for AI Coding Assistants

This file describes the structure and rules of the **Single Repo Data Platform (SRDP)** repository.

---

## What this repository is

SRDP assembles a modern open-source data platform (Zitadel, Traefik, Dagster, Polars/DuckDB, marimo, Quarto) into a single Git repository with two deployment targets: Docker Compose and Kubernetes (Helm + Scaleway Kapsule).

---

## Repository layout

```
srdp/
├── src/srdp/              # Platform library (IO managers, resources, base patterns)
├── projects/              # Client ETL projects (one per client/domain)
├── services/              # Generic platform runtimes (quarto), no tenant content
├── deploy/                # Deployment manifests (no business logic)
│   ├── docker/            # Docker Compose stack
│   ├── kubernetes/        # Helm umbrella chart
│   └── opentofu/          # GCP and Scaleway provisioning
├── config/                # Env-specific config (secrets gitignored)
└── docs/                  # Documentation source (Zensical)
```

---

## Hard rules

- Never commit secrets (`.env`, `secrets.sh`, `values-prod.yaml`, `kubeconfig.yaml`).
- Always use `just` as the task runner and `uv` as the package manager.
- No `pip`, `conda`, or `requirements.txt`.
- No `from __future__ import annotations`. Use native type hints (`str | None`, `list[int]`).
- No `print()`. Use `logging.getLogger(__name__)`.
- No bare `except:`. Always catch a specific exception type.
- No relative imports. Always use absolute imports.
- All configuration via Pydantic Settings, never `os.environ` directly.
- Reusable platform code belongs in `src/srdp/`, not in projects.
- Tenant-specific app content belongs in `projects/<name>/`, not `services/`. This includes pipeline code and also a UI like a Streamlit dashboard or a Marimo notebook. `services/` is only for generic runtimes any project could reuse unchanged with no project-specific content baked in (quarto). Follow the `projects/<name>/Dockerfile` precedent: the app's Dockerfile and content live together in the project. `deploy/docker/docker-compose.yml` owns the orchestration wiring.
- `requires-python` in `pyproject.toml` has an upper bound (`<3.13`) on purpose. Do not loosen it. An open-ended `>=3.12` lets `uv` pick the newest interpreter satisfying it, and some deps (e.g. `dbt-core`) lag behind new Python releases.
- Dockerfiles: `uv sync` with explicit extras (`--extra x --extra y`), never `--all-extras`. It pulls in unrelated dev-only deps and can break the build.
- Local domains are `*.srdp.localhost`, not `.local.dev`. The `.localhost` TLD auto-resolves to loopback, so no `/etc/hosts` edit is needed.

## Markdown

Full rules live in [`markdown.instructions.md`](.github/instructions/markdown.instructions.md) and are hook-enforced (`.claude/settings.json`, `PostToolUse` on `Write|Edit`): no em dashes ever, full sentences, no comma-spliced fragments, no "not X but Y" contrastive tics, no colon-into-fragment constructions. Check `zensical.toml` before suggesting markdown formatting or structure under `docs/`.

---

## Detailed instructions

Domain-specific conventions are in `.github/instructions/` and load automatically based on file context:

| File | Applies to |
|:---|:---|
| [`python.instructions.md`](.github/instructions/python.instructions.md) | All `*.py` files |
| [`dagster.instructions.md`](.github/instructions/dagster.instructions.md) | Dagster assets, definitions, IO managers |
| [`deploy.instructions.md`](.github/instructions/deploy.instructions.md) | Deploy manifests, services, Justfile |
| [`markdown.instructions.md`](.github/instructions/markdown.instructions.md) | All `*.md`, `*.mdx`, `*.qmd` files |
