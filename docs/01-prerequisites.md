---
title: 1. Prerequisites
icon: lucide/list-todo
---

# Prerequisites

Before you begin, ensure you have the following software installed on your local machine. This guide assumes you have a basic understanding of using the command line.

### Required tools (all deployment methods)

Install each tool following its own instructions.

- [Git](https://git-scm.com/downloads).
- A container runtime with Docker Engine and Docker Compose 2.23 or newer, which the Compose stack's inline Garage config needs ([install](https://docs.docker.com/engine/install/)).
  Any Docker-compatible setup works, and Colima and OrbStack are two that are known to work.
- [`just`](https://github.com/casey/just), the task runner for common commands.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/), the Python package manager.

### Local development

- [`mkcert`](https://github.com/FiloSottile/mkcert), for locally trusted TLS certificates for `*.srdp.localhost`.
  `just docker-tls` and `just local-tls` install its local CA and generate the certificates.
  The first run may ask for your password, because it adds the CA to your system trust store.

### Kubernetes

- [`kubectl`](https://kubernetes.io/docs/tasks/tools/), within one minor version of your cluster (kind 1.32+ locally).
- [Helm 3.x](https://helm.sh/docs/intro/install/).
- [`kind`](https://kind.sigs.k8s.io/docs/user/quick-start/#installation), for a local Kubernetes cluster (tested with 1.32+).
  It runs as plain containers on the Docker daemon you already have, with no separate VM, and works the same on macOS, Linux and Windows.

### Additional tools for production

- [OpenTofu](https://opentofu.org/docs/intro/install/), for infrastructure provisioning on Scaleway Kapsule.

### Tested with

- Local Kubernetes: `kind`, same setup on macOS and Linux.
- Docker Compose: any Docker Engine-compatible runtime.

### Notes

- The `just` recipes require a Bash-compatible shell.
- You need permission to create namespaces/secrets and, for cloud runs, to provision load balancers.
