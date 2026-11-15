# AGENTS.md: rules for AI coding assistants in SRDP

SRDP is a composable, open-source data stack in one Git repository.
Its core is Dagster, DuckLake and FastAPI.
It runs on two targets, Docker Compose and Kubernetes (Helm).
The goal is a secure platform that one command deploys with the settings in `srdp.toml`.

## The most important rules

- Keep the platform secure by default. Only an explicit setting in `srdp.toml` makes a rule weaker.
- Change Docker Compose and Kubernetes together, because they must not diverge.
- Keep SRDP inside its own unit, which is the Compose host or the SRDP namespace.
- Put no cloud provider name in platform code, values or defaults.
- Never commit a secret, also not as a dev example. Never hardcode credentials, IP addresses or hostnames.
- Read every credential from its secret with a fixed name in `deploy/secrets.toml`. Put no secret in `srdp.toml`.
- Never commit, push or write to GitHub without explicit permission for each action.
- Report only what you tested. Name the target, the version, and fresh install or upgrade.

## Repository layout

| Path | Contents |
|:---|:---|
| `src/srdp/` | The platform library, used by every project |
| `projects/<name>/` | One client project with its pipelines, apps and Dockerfile |
| `services/` | Generic runtimes that every project can use unchanged |
| `deploy/docker/`, `deploy/kubernetes/` | The Compose stack and the Helm chart |
| `docs/` | The documentation site (Zensical) |

## Tools

- Use `uv` for Python and `just` for tasks. Never use `pip`, `conda` or `requirements.txt`.
- Read `pyproject.toml` before you write code. It sets the rules for ruff, ty and pytest.
- Build every runtime image on a hardened base image (`dhi.io`), pinned by digest. A build stage can use another pinned image.

## Review

Check every script, workflow and manifest for these four problems.

- Injection: quote every dynamic value in shell, queries and templates.
- Least privilege: give containers, credentials and grants the minimum access.
- State consistency: change mirrored values together.
- Failure order: validate before you change anything.

## Git

- Name a branch `<type>/<issue-number>-<slug>`, with a Conventional Commits type.
- Open an issue first for a feature or a fix. A chore needs no issue and uses `chore/<slug>`.

## Detailed instructions

Follow the file for the files you change.
Claude Code imports them through the lines below.

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
