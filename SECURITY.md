# Security Policy

## Reporting a vulnerability

Please do not report security vulnerabilities through public GitHub issues.

Use [GitHub's private vulnerability reporting](https://github.com/srdp-hub/srdp/security/advisories/new) to open a security advisory. This keeps the disclosure confidential while we assess and address it.

Include as much of the following as possible:

- A description of the vulnerability and its potential impact
- Steps to reproduce or a proof-of-concept
- Affected versions or components
- Any suggested mitigations

We aim to acknowledge reports within 72 hours and will keep you informed as we work on a fix.

The [security page](https://docs.srdphub.com/security/) describes how SRDP builds and hardens the platform.

## Supported versions

Only the latest release is actively maintained. We do not backport security fixes to older versions.

The supported Python runtime is **3.12**, and 3.13 is planned. Other Python versions will not receive fixes.

## Scope

This policy covers the SRDP platform library (`src/srdp/`), deployment manifests (`deploy/`), and service images (`services/`).

Issues in third-party dependencies (Dagster, Zitadel, Traefik, etc.) should be reported upstream to the respective projects.
