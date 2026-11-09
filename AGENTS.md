# AGENTS.md: rules for AI coding assistants in SRDP

## What SRDP is

SRDP is a composable, open-source data stack in one Git repository.
Its core is Dagster, DuckLake and FastAPI.
It runs on two targets, Docker Compose and Kubernetes (Helm).
The goal is a secure platform that one command deploys with the settings in `srdp.toml`.

## The most important rules

- Keep the platform secure by default. Only an explicit setting in `srdp.toml` makes a rule weaker.
- Change Docker Compose and Kubernetes together, because they must not diverge.
- Keep SRDP inside its own unit, which is the Compose host or the SRDP namespace.
- Put no cloud provider name in platform code, values or defaults.
- Never commit a secret, also not as a dev example.
- Report only what you tested.
- Never commit, push or write to GitHub without explicit permission for each action.

## Repository layout

| Path | Contents |
|:---|:---|
| `src/srdp/` | The platform library, used by every project |
| `projects/<name>/` | One client project with its pipelines, apps, notebooks and Dockerfile |
| `services/` | Generic runtimes that every project can use unchanged, such as `hub` and `quarto` |
| `deploy/docker/` | The Docker Compose stack |
| `deploy/kubernetes/` | The Helm chart |
| `deploy/secrets.toml` | The fixed name and keys of every credential |
| `config/` | Configuration of components, without secrets |
| `docs/` | The documentation site (Zensical) |

## Secrets and configuration

- Never hardcode credentials, IP addresses or hostnames of an environment.
- Read every credential from the secret with its fixed name in `deploy/secrets.toml`.
- Put no secret in `srdp.toml`.
- Read configuration in Python through Pydantic Settings, never through `os.environ`.
- If an app default seems to need a literal value, check that first.

## Tools

- Read `pyproject.toml` before you write code. It sets the rules for ruff, ty and pytest.
- Run every Python command through `uv`, such as `uv run pytest`.
- Use `just` to run tasks. Never use `pip`, `conda` or `requirements.txt`.
- Keep `requires-python` below `3.14`, because some dependencies lag behind.
- Build every image on a hardened base image (`dhi.io`), pinned by digest.
- In a Dockerfile, run `uv sync` with explicit extras. Never use `--all-extras`.

## Python

- Follow [`python.instructions.md`](.github/instructions/python.instructions.md) for every Python file.
- Write tests with pytest.
- Use native type hints, such as `str | None`. Never add `from __future__ import annotations`.
- Log with `logging.getLogger(__name__)`. Never use `print()`.
- Catch a specific exception. Never use a bare `except:`.
- Use absolute imports only.
- Keep classes stateless. Return values and do not change arguments.
- Use Pydantic models for settings and configuration.
- Write efficient code. Use lazy Polars and DuckLake queries, vectorized operations, and async for I/O.

## Review

Check every script, workflow and manifest for these four problems.

- Injection: quote every dynamic value in shell, queries and templates.
- Least privilege: give containers, credentials and grants the minimum access.
- State consistency: change mirrored values together.
- Failure order: validate before you change anything.

## Git and reporting

- Name a branch `<type>/<issue-number>-<slug>`, with a Conventional Commits type. A chore needs no issue.
- Open an issue first for a feature or a fix.
- In a report, name the target, the component version, and fresh install or upgrade.
- Put everything you did not test under "not verified".

## Markdown

- Follow [`markdown.instructions.md`](.github/instructions/markdown.instructions.md) for every Markdown file.
- Use ASD-STE100 in procedures and instruction files, and plain technical English in ADRs.
- Write full sentences, one per line.

## Detailed instructions

`.github/instructions/` has the rules for each domain.
GitHub Copilot loads each file through its `applyTo` globs.
Claude Code reads this file and imports the files through the lines below.
Other assistants must read the files that apply.

@.github/instructions/python.instructions.md
@.github/instructions/dagster.instructions.md
@.github/instructions/deploy.instructions.md
@.github/instructions/markdown.instructions.md

| File | Applies to |
|:---|:---|
| [`python.instructions.md`](.github/instructions/python.instructions.md) | All `*.py` files |
| [`dagster.instructions.md`](.github/instructions/dagster.instructions.md) | Dagster assets, definitions and IO managers |
| [`deploy.instructions.md`](.github/instructions/deploy.instructions.md) | Deploy manifests, services and the Justfile |
| [`markdown.instructions.md`](.github/instructions/markdown.instructions.md) | All `*.md`, `*.mdx` and `*.qmd` files |
