# Contributing

Thank you for your interest in contributing to SRDP.

## Getting started

1. Check the [open issues](https://github.com/srdp-hub/srdp/issues) to see if your idea or bug is already tracked.
2. For new features or significant changes, open an issue to discuss before submitting a PR.
3. Fork the repository and create a branch from `main`, named `<type>/<issue-number>-<short-slug>` (e.g. `feat/58-git-cliff-adoption`). `<type>` matches the Conventional Commits types used for commits and PR titles: `feat`, `fix`, `docs`, `chore`, `refactor`, `perf`, `test`, `ci`, `revert`.

## Development setup

SRDP uses [`just`](https://github.com/casey/just) as the task runner and [`uv`](https://docs.astral.sh/uv/) for Python package management.

```bash
just init   # install deps and set up pre-commit
just --list # see all available commands
```

## Code conventions

Conventions are documented in [`AGENTS.md`](AGENTS.md) at the repository root. Read it before making changes. Key rules:

- No secrets, credentials, or sensitive data in commits.
- No `pip` or `conda`, use `uv`.
- All configuration via Pydantic Settings, never `os.environ` directly.
- Use `logging.getLogger(__name__)` instead of `print()`.
- No relative imports.

## AI-assisted contributions

We allow AI assistance in code generation and documentation, but all responsibility lies with the contributor. For more details, see [AI-assisted contribution guidelines](https://docs.srdphub.com/07-contributing/#ai-assisted-contributions).

## Submitting changes

- Keep PRs focused on a single concern.
- Write a clear description and link any related issues using `Closes #...`.
- Ensure all CI checks pass: `just ci`.

## Reporting security issues

See [SECURITY.md](SECURITY.md) for how to report vulnerabilities privately.
