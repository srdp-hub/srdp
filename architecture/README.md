# SRDP architecture model

This folder holds the C4 model of SRDP, written in [LikeC4](https://likec4.dev/).
It shows how the platform is designed, who uses it and where it runs.
It describes the target design, so planned parts appear next to the parts that run today.

## Commands

Run these from the repository root. They need Node.js.

- `just c4-serve` starts the viewer on port 5173 with live reload.
- `just c4-validate` checks the model. It fails on errors and on warnings.

## Files in `src/`

| File | Content |
|:---|:---|
| `_spec.c4` | Element kinds and tags. |
| `model.c4` | The logical model: people, systems, containers and their relationships. |
| `dagster.c4` | The concepts of Dagster. |
| `model.views.c4` | The context, container and component views. |
| `deployment.spec.c4` | The kinds of deployment node. |
| `deployment.c4` | Where the services run: a single machine and a cloud cluster. |
| `deployment.views.c4` | The deployment views. |
| `dynamic.views.c4` | The start order of a deployment and the two ingestion paths. |

## Views in the viewer

The overview is at the root. The other views sit in folders that follow the C4 levels.

1. Context and containers.
2. Components.
3. Deployment.
4. Sequences.

## Tags

- `planned` marks a part that is not in production yet. Views draw it with a dashed border.
- `open` marks a decision that is still open for discussion. Views draw it with a dotted amber border.
- `adr-NNNN` marks the ADRs that relate to an element. The element links to each ADR on GitHub.

## Rules for changes

- Write each description in at most 25 words, with short sentences in the active voice.
- Model the design of the platform. Leave out the development process, such as pull requests and CI.
- Add an `adr-NNNN` tag and a link for every ADR that relates to a new element.
- Style deployment views by path. LikeC4 does not support tag styles there.
- Run `just c4-validate` before you commit.
