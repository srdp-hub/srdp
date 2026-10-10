---
title: 7. Contributing
icon: lucide/git-pull-request
---

# Contributing

## Getting started

```bash
git clone https://github.com/srdp-hub/srdp.git
cd srdp
just init   # install dependencies and set up pre-commit
just ci     # lint, type check and tests
```

`just --list` shows all available commands.

## GitHub labels

Labels are managed in the GitHub UI under **Settings > Labels**. The set is intentionally small.

| Label | Color | Description |
|:---|:---|:---|
| `bug` | `#E99695` | Something isn't working |
| `enhancement` | `#a2eeef` | New feature or improvement |
| `documentation` | `#0075ca` | Improvements to docs |
| `performance` | `#340E3F` | Performance issues or improvements |
| `regression` | `#6109D4` | Broken by a recent change |
| `breaking` | `#D47928` | Introduces a breaking change |
| `good first issue` | `#7057ff` | Good for newcomers |
| `needs repro` | `#FBCA04` | Bug needs a reproducible example |
| `needs decision` | `#5319E7` | Needs maintainer decision before proceeding |
| `wontfix` | `#aaaaaa` | This will not be worked on |
| `duplicate` | `#cfd3d7` | Already tracked elsewhere |

## Branching

We use GitHub Flow: feature branches from `main`, merged via PR.

Branch naming: `<type>/<issue-number>-<short-slug>` (e.g. `fix/123-short-desc`), where `<type>` is a Conventional Commits type such as `feat`, `fix`, `docs` or `chore`.
Open an issue first if none exists.

## Pull requests

- Describe what the PR does and why.
- Keep PRs focused on one logical change.
- Ensure CI passes.
- Update docs if behavior changes.

## Versioning

We follow [Semantic Versioning](https://semver.org/):

- **Patch**: bug fixes, security dependency bumps.
- **Minor**: new features, non-breaking changes.
- **Major**: breaking changes to public APIs or data formats.

While SRDP is at 0.x, a minor release may contain breaking changes, and the changelog says what to change when it does.
Documentation-only changes, CI updates, and test additions do not bump the version.

## Releasing

A maintainer cuts a release from `main` with `just release <version>`, for example `just release 0.4.0`.
The script bumps the version in `pyproject.toml` and the lockfile, renames the `[Unreleased]` section of `CHANGELOG.md`, runs `just ci`, commits, and tags.
Push the commit and the tag, and `release.yml` drafts a GitHub Release.
Publishing that draft builds, scans and signs the platform images, and publishes the package to PyPI.

A release candidate such as `just release 0.4.0-rc.1` leaves the changelog alone, and its draft is marked as a pre-release.
It publishes the images with the exact version tag only, so `latest` stays on the last stable release.

GitHub creates new container packages as private, and the chart pulls the platform images without credentials.
After the first publish, set each package to public in its settings. The `public` job of `images.yml` fails after a release while one is still private.

## Architectural Decision Records (ADRs)

Significant, hard-to-reverse decisions (choosing a component, changing a core interface, adopting a new pattern) should be recorded as an ADR in `docs/adr/`.

**When to write one:**

- You're choosing between multiple realistic options with real trade-offs.
- The decision will be difficult or costly to reverse later.
- Future contributors would otherwise wonder *why* the project is structured this way.

**When you don't need one:** bug fixes, refactors that don't change behaviour, dependency bumps, or any decision that's obvious from the code.

**How to add an ADR:**

1. Copy [`.github/adr-template.md`](https://github.com/srdp-hub/srdp/blob/main/.github/adr-template.md) to `docs/adr/NNNN-short-title.md`, using the next available number. (The template lives outside `docs/` so the docs site does not render it.)
2. Fill in the context, options considered, and the chosen outcome with justification.
3. Set `status: proposed` in the frontmatter; it moves to `accepted` once the PR merges.

We follow the [MADR](https://adr.github.io/madr/) format.

## CI/CD

CI runs on GitHub Actions, and the workflows live in `.github/workflows/`.

- **`ci.yml`** runs the pre-commit hooks and the tests on every pull request and every push to `main`.
- **`images.yml`** builds and scans the platform images on pull requests and pushes to `main`, and publishes them to ghcr.io when a release is published.
- **`docs.yml`** builds and deploys this documentation to GitHub Pages.
- **`release.yml`** and **`publish.yml`** create a release from a version tag and publish the package to PyPI.

Production deployments can also be triggered manually via `just prod-full` as a fallback.

## AI-assisted contributions

We follow the [Linux Foundation policy on generative AI](https://www.linuxfoundation.org/legal/generative-ai): AI-generated code is treated the same as any other contribution.

1. **You own your commits.** Review, understand, and validate before committing.
2. **License compliance.** Ensure AI output does not conflict with Apache-2.0.
3. **No special process.** Same PR review as any other change.

### Agent configuration

- [`AGENTS.md`](https://github.com/srdp-hub/srdp/blob/main/AGENTS.md) at the repo root provides the project overview, hard rules, and links to specifics.
- `.github/instructions/*.md` contains domain-specific instructions that load based on file patterns.
- `.github/copilot-instructions.md` references `AGENTS.md` for GitHub Copilot integration.

### Guidelines for maintaining agent instructions

The top-level `AGENTS.md` should stay concise (ideally ~50 lines). Overloaded instruction files cause agents to lose focus and ignore important rules.
**Structure it hierarchically:**

1. `AGENTS.md` contains only the essentials: one-line project description, condensed repo layout, 5-10 hard rules, and links to detail files.
2. `.github/instructions/` files contain domain-specific conventions (Python style, Dagster patterns, Helm and deployment context). Each file declares an `applyTo` glob so it only loads when the agent is working on matching files.

**Good candidates for agent instructions:**

- Project-specific constraints an agent cannot infer from config or code (e.g., "always use `uv`, never `pip`").
- Framework conventions with one concise example.
- Hard boundaries ("never commit secrets", "no bare `except:`").

**Avoid putting these in agent instructions:**

- Anything already expressed in `pyproject.toml`, ruff config, or linter rules.
- Full documentation or runbooks. Link to the docs site instead.
- Version numbers or URLs that change frequently.
