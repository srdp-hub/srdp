---
title: Security in our DevOps process
icon: lucide/shield-check
---

# Security in our DevOps process

SRDP follows three security principles.
Every container gets the least privilege it needs.
Every setting is secure by default.
Everything SRDP builds can be traced to its source.

## Building SRDP

### Development

- Pre-commit hooks run before each commit. They look for secrets and private keys and check code style, types and YAML.
- `just test` runs the tests that keep Docker Compose, the Helm chart and `srdp.toml` consistent.
- Contributors and AI assistants follow a review checklist for injection, least privilege, state consistency and failure order.

### Pull Request Gates

- CI runs the same hooks on all files, so skipping them locally does not get a change through.
- A change reaches `main` only through a pull request with one approval and resolved review conversations.
- A new push dismisses earlier approvals.
- Direct pushes, force pushes and deletion of `main` are blocked for everyone.

### Dependencies and Updates

Dependencies are frozen, so a build contains exactly what was reviewed.

- `uv.lock` holds a hash for every Python package file. A package that differs from the locked one stops the build.
- Every Python image installs exactly the versions in `uv.lock`. The build fails when the lock file is out of date.
- Base images, `uv` and third-party images are pinned by digest.
- GitHub Actions and pre-commit hooks are pinned by commit SHA.
- Tests fail on a base or third-party image without a digest and on an Action or hook without a commit SHA.
- Renovate opens pull requests for new image and chart versions, and Dependabot does the same for GitHub Actions. A maintainer approves each one.

### Container Images

The runtime stage of every image is a Docker Hardened Image, so an attacker finds little to use.

- The example project images follow the same pattern as the platform images.
- Dagster sends no usage data.

### Code and Image Scanning

- Trivy scans the images that SRDP builds on every pull request that changes them, and every day.
- Trivy scans the platform images again before a release, and a critical finding stops the release.
- A pull request fails on a critical vulnerability that has a fix.
- The daily scan reports to the GitHub Security tab and does not fail the build.
- Dependabot alerts on vulnerable Python packages.
- GitHub scans every push for secrets and blocks known credential formats.
- Each accepted risk sits in `.trivyignore` with a reason and an expiry date.

### Release Evidence

Every published platform image comes with evidence of where it came from and what it contains.
This evidence consists of build attestations.

- The signature shows that the image came from the SRDP build workflow and that nobody changed it afterwards.
- The build provenance records the commit, the workflow and the time of the build.
- The SBOM, a list of every package inside the image, ships with the image.
- Images are pushed by digest. Version tags are set last, so a tag never points at an image that failed a check.

### Verify It Yourself

- Check the signature and the build provenance as [Cloud Deployment](06-deployment.md#verifying-published-images) describes.
- Read the SBOM with `docker buildx imagetools inspect ghcr.io/srdp-hub/<image>:<version> --format '{{ json .SBOM }}'`.

## Running SRDP

### Least Privilege

Every part of SRDP gets only the access it needs.
SRDP applies this to what an image contains, who runs it, what it can change and which credentials it holds.

- A builder stage installs the packages, and the runtime stage has no package manager.
- The runtime stage has no shell, except in the Dagster image. The upstream Helm chart starts Dagster pods with `bash`.
- Every image that SRDP builds runs as a non-root user.
- The SRDP services, Traefik, oauth2-proxy and Marquez have a read-only root filesystem, no capabilities and no privilege escalation.
- Each service reads its credentials from its own Secret.
- With S3 storage, the apps get a read-only key. Only Dagster and the setup service get the key that can write.

## Reporting and Standards

Report a vulnerability through [GitHub's private vulnerability reporting](https://github.com/srdp-hub/srdp/security/advisories/new).
The [security policy](https://github.com/srdp-hub/srdp/blob/main/SECURITY.md) has the details.
SRDP does not claim compliance with a standard.
The controls on this page can support the supply chain part of an assessment under NEN 7510, ISO 27001, NIS2 or the EU Cyber Resilience Act.
