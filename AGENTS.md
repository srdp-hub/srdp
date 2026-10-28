# AGENTS.md: SRDP guide for AI coding assistants

This file gives the structure and rules of the Single-Repo Data Platform (SRDP) repository.

## What this repository is

SRDP puts an open-source data platform into one Git repository.
The platform uses Zitadel, Traefik, Dagster, Polars, DuckDB and marimo.
It has two deployment targets, Docker Compose and Kubernetes (Helm on Scaleway Kapsule).

Every change must move SRDP toward a hardened, configurable, production-ready platform.
The goal is one command that deploys it with standard settings from `srdp.toml`.

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

## Hard rules

### Secrets and credentials

- Never commit secrets, such as `.env`, `secrets.sh`, `values-prod.yaml` or `kubeconfig.yaml`.
- Never hardcode credentials, IP addresses or environment-specific hostnames in scripts, manifests or app config.
- Read these values from env vars, `.env` or chart values.
- If an app default seems to need a literal value, check that first. Do not assume it.

### Tools

- Use `just` as the task runner and `uv` as the package manager.
- Do not use `pip`, `conda` or `requirements.txt`.
- Keep the upper bound `<3.13` on `requires-python` in `pyproject.toml`. Some dependencies, such as `dbt-core`, lag behind new Python releases.
- In Dockerfiles, run `uv sync` with explicit extras (`--extra x --extra y`). Never use `--all-extras`, because it adds dev-only dependencies.

### Python

- Do not use `from __future__ import annotations`. Use native type hints, such as `str | None`.
- Do not use `print()`. Use `logging.getLogger(__name__)`.
- Do not use a bare `except:`. Catch a specific exception type.
- Use absolute imports only.
- Read all configuration through Pydantic Settings. Do not read `os.environ`.

### Code location

- Put reusable platform code in `src/srdp/`.
- Put tenant-specific content in `projects/<name>/`, including pipelines, Streamlit dashboards and marimo notebooks.
- Use `services/` only for generic runtimes that any project can reuse unchanged, such as quarto.
- Keep an app's Dockerfile with its content in the project, as `projects/<name>/Dockerfile` does.
- Wire services together in `deploy/docker/docker-compose.yml`.

### Deployment

- Use `*.srdp.localhost` for local domains. The `.localhost` TLD resolves to loopback without `/etc/hosts` edits.
- Change Docker Compose and Kubernetes together. A change to one target only makes them diverge.

### Review

Check scripts, workflows and manifests for these four problems.

- Injection: dynamic values in shell, queries or templates must be quoted.
- Least privilege: containers, credentials and grants get the minimum access.
- State consistency: mirrored values change together.
- Failure order: validate before you change anything.

### Git and reporting

- Name branches `<type>/<issue-number>-<short-slug>`, such as `fix/123-short-desc`. `<type>` is a Conventional Commits type.
- Open an issue first if none exists.
- Report only what you tested. Name the target (Compose or Kubernetes), the component version, and fresh install or upgrade.
- Put everything else under "not verified".

## Markdown

[`markdown.instructions.md`](.github/instructions/markdown.instructions.md) has the full rules, and hooks in `.claude/settings.json` enforce them.
Use the ASD-STE100 style. Follow it strictly in procedures and instruction files.
Never use em dashes.
Write full sentences. Do not join fragments with commas.
Do not use "not X but Y" contrasts or a colon followed by a fragment.
Check `zensical.toml` before you suggest formatting or structure under `docs/`.

## Detailed instructions

`.github/instructions/` has the rules for each domain.
GitHub Copilot loads each file through its `applyTo` globs.
Claude Code imports all of them through `CLAUDE.md`.
Other assistants must read the files that apply.

| File | Applies to |
|:---|:---|
| [`python.instructions.md`](.github/instructions/python.instructions.md) | All `*.py` files |
| [`dagster.instructions.md`](.github/instructions/dagster.instructions.md) | Dagster assets, definitions, IO managers |
| [`deploy.instructions.md`](.github/instructions/deploy.instructions.md) | Deploy manifests, services, Justfile |
| [`markdown.instructions.md`](.github/instructions/markdown.instructions.md) | All `*.md`, `*.mdx`, `*.qmd` files |
