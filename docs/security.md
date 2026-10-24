---
title: Security in our DevOps process
icon: lucide/shield-check
---

# Security in our DevOps process

SRDP builds security into each step of its DevOps process.
This page lists the main mechanisms, the step where each one applies and the reason for it.
A mechanism with the status Planned is not active yet.

## Mechanisms

| Step | Mechanism | Why | Status |
|:---|:---|:---|:---|
| Commit | Pre-commit hooks: gitleaks, private key detection, ruff, ty, yamllint, tflint | Find secrets and mistakes before anyone else sees them. | In place |
| Push | GitHub secret scanning with push protection | Block known credential formats from reaching the repository. | In place |
| Pull request | Tests that keep Docker Compose, the Helm chart and `srdp.toml` consistent | A change to one deployment target must not break the other. | In place |
| Pull request | Review checklist: injection, least privilege, state consistency, failure order | Catch the typical mistakes in scripts, workflows and manifests. | In place |
| Pull request | Required review and passing checks on `main` | Make sure no change merges unreviewed. | Planned |
| Build | Frozen dependencies: `uv.lock` holds a hash for every package file | Every image contains exactly the packages that were reviewed, and a swapped package stops the build. | In place |
| Build | Base images, `uv` and third-party images pinned by digest | A tag can move to a different image and a digest cannot. | Partly. Some chart images still use tags. |
| Build | Actions and hooks pinned by commit SHA | Code that a moved tag could change must not run in our pipeline. | Partly. Two of six workflows. |
| Build | Hardened runtime images with no shell and no package manager, running as non-root | An attacker finds little to use and can do little damage. | In place for four images |
| Build | Hardened images and the scan for the project images and `srdp-api` | The same protection for every image we ship. | Planned |
| Scan | Trivy on every image: fails a pull request on a critical vulnerability with a fix | Stop a known weakness before it merges. | In place |
| Scan | Daily Trivy scan of `main` with the results in the Security tab | New vulnerabilities appear without a code change. | In place |
| Scan | More Trivy modes: `fs` for the lock, `config` for manifests, `sbom` for released images | Cover dependencies and configuration, and re-check shipped images. | Planned |
| Scan | `.trivyignore` with a reason and an expiry date for every accepted risk | An accepted risk gets looked at again. | In place |
| Update | Renovate and Dependabot open pull requests | Stay current, with a person approving each version change. | In place |
| Release | Images pushed by digest, scanned, signed, with build provenance and an SBOM. Version tags come last. | Prove who built an image and what it contains. A tag never points at an image that failed a check. | In place |
| Release | Evidence bundle per release: image digests, SBOMs and scan reports | Give reviewers a fixed record per release. | Planned |
| Deploy | Verify the signature before the first pull, as [Cloud Deployment](06-deployment.md) shows | Run only what SRDP built. | In place |
| Deploy | Signed Compose file and Helm chart with every image resolved to its digest | Deployments pull by digest and never by tag. | Planned |
| Run | Configurable `uid` and `gid` in `srdp.toml` for every container | Let the host limit the whole stack to one user. | Planned |

The `latest` tag exists for people who try SRDP out.
No build, test or deploy flow of SRDP uses it.

## Verify it yourself

- Check the signature of an image as [Cloud Deployment](06-deployment.md) describes.
- Check the build provenance with `gh attestation verify oci://ghcr.io/srdp-hub/<image>@<digest> --owner srdp-hub`.
- Read the SBOM with `docker buildx imagetools inspect ghcr.io/srdp-hub/<image>@<digest> --format '{{ json .SBOM }}'`.
- Open the Images workflow run of a release in the Actions tab for the scan results.

## Report a vulnerability

Use GitHub's private vulnerability reporting, as `SECURITY.md` describes.
SRDP does not claim compliance with a standard, and the controls here support the supply chain parts of requirements such as NEN 7510, ISO 27001, NIS2 and the EU Cyber Resilience Act.
