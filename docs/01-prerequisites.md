---
title: 1. Prerequisites
icon: lucide/list-todo
---

# Prerequisites

Before you begin, ensure you have the following software installed on your local machine. This guide assumes you have a basic understanding of using the command line.

### Required tools (all deployment methods)

- Git
- Container runtime (Docker Engine + Docker Compose). Any Docker-compatible setup works, this repo doesn't assume one, Colima and OrbStack are two that are known to work.
- [`just`](https://github.com/casey/just), task runner for common commands
- [`mkcert`](https://github.com/FiloSottaro/mkcert), local TLS certificates for `*.srdp.localhost`

- `kubectl` + Helm 3.x
- [`kind`](https://kind.sigs.k8s.io/), for a local Kubernetes cluster (tested with 1.32+). Runs as plain containers on whatever Docker daemon you already have, no separate VM, and works identically on macOS, Linux, and Windows.

### Additional tools for production

- [OpenTofu](https://opentofu.org/docs/intro/install/), infrastructure provisioning on Scaleway Kapsule

### Tested with

- Local Kubernetes: `kind`, same setup on macOS and Linux.
- Docker Compose: any Docker Engine-compatible runtime.

### Notes

- The `just` recipes require a Bash-compatible shell.
- You need permission to create namespaces/secrets and, for cloud runs, to provision load balancers.
