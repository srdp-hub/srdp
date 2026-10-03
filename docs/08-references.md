---
title: 8. OSS Tool References
icon: lucide/link
---

# OSS Tool References

This document holds references to the sources used in the creation of this project.

## SSO and Identity Management

- **[Self-hosting SSO with Traefik (Part 1): Keycloak](https://joeeey.com/blog/selfhosting-sso-with-traefik-keycloak-part-1/)**
    - This blog post provides a comprehensive guide on setting up a self-hosted Single Sign-On (SSO) solution using Keycloak with Traefik as the reverse proxy. The main takeaway was the setup for the oauth2 proxy and securing services.

- **[Configure Zitadel with Traefik | ZITADEL Docs](https://zitadel.com/docs/self-hosting/manage/reverseproxy/traefik)**
    - This official documentation from ZITADEL explains how to configure Zitadel with Traefik as a reverse proxy. It offers practical examples using Docker Compose and provides configurations for different TLS modes, including "TLS mode external" where Traefik terminates TLS, and "TLS mode enabled" for end-to-end encryption. We used the "TLS mode external" option, where Traefik terminates TLS and Zitadel runs with `TLS.Enabled: false`.

## Reverse Proxy and Configuration

- **[Setup Traefik Proxy in Docker Standalone - Traefik](https://doc.traefik.io/traefik/setup/docker/)**
    - The official Traefik documentation provides a detailed walkthrough for installing and configuring Traefik Proxy within a Docker container using Docker Compose. It covers enabling the Docker provider, exposing HTTP and HTTPS entrypoints, redirecting HTTP to HTTPS, and deploying a sample application.

- **[Overview | OAuth2 Proxy](https://oauth2-proxy.github.io/oauth2-proxy/configuration/overview)**
    - This documentation provides a complete overview of the configuration options for OAuth2 Proxy. It details how to configure the proxy via command-line options. The page lists all available flags for general provider options, cookie settings, header options, logging, and more.

## External Identity Providers

- **[Integrate Identity Providers | ZITADEL Docs](https://zitadel.com/docs/guides/integrate/identity-providers/introduction)**
    - This ZITADEL documentation provides a high-level guide on how to connect external identity providers (IdPs). It outlines the process of adding providers at the instance level and then making them available to organizations through login policies, which was the exact workflow followed for Google and GitHub.

- **[Setting up OAuth 2.0 | Google Cloud Documentation](https://support.google.com/cloud/answer/6158849)**
    - The official Google Cloud documentation was used to complete the necessary steps for creating an OAuth 2.0 application. This included configuring the consent screen, defining scopes, and generating the Client ID and Client Secret required by ZITADEL.

- **[Creating an OAuth App | GitHub Docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app)**
    - This guide from GitHub's official documentation details the process of registering a new OAuth application. It was followed to obtain the Client ID and to generate a Client Secret, which were then used to configure GitHub as an external identity provider within ZITADEL.

## Multi-Factor Authentication (MFA)

- **[Default settings, login and MFA | ZITADEL Docs](https://zitadel.com/docs/guides/manage/console/default-settings)**
    - This official ZITADEL documentation explains how to manage login security policies for an organization. It was used as the primary reference for enforcing Multi-Factor Authentication by changing the organization's policy to "Required" and confirming that TOTP was an allowed second factor.

## Database

- **[Bitnami PostgreSQL Helm Chart | GitHub](https://github.com/bitnami/charts/tree/main/bitnami/postgresql)**
    - The Bitnami PostgreSQL Helm chart provides the in-cluster database, aliased as `zitadel-db` in the umbrella chart.
      Its `auth.*` fields create the `zitadel` database and role; the `srdp-setup` Job creates the other service databases.
      The chart also handles TLS, replication, and scheduled backups via CronJob.

## Data Orchestration

- **[Dagster Documentation](https://docs.dagster.io/)**
    - The official Dagster documentation was the primary reference for setting up the data orchestration layer. It covers the asset-based programming model (used for the srdp-etl pipeline), the webserver/daemon architecture, user code deployments via gRPC, and Helm-based deployment on Kubernetes with external PostgreSQL and the K8sRunLauncher.

- **[Customizing your Kubernetes deployment | Dagster Docs](https://docs.dagster.io/deployment/oss/deployment-options/kubernetes/customizing-your-deployment#per-job-kubernetes-configuration)**
    - This Dagster guide documents how `dagster-k8s/config` can be applied at deployment, code location, job, and step scope. It was used to understand how per-job Kubernetes configuration works with the `K8sRunLauncher`, including resource requests and limits, labels, and precedence and merge behavior across configuration layers.

- **[Customizing run queue priority | Dagster Docs](https://docs.dagster.io/deployment/execution/customizing-run-queue-priority)**
    - This Dagster guide explains how queued runs are ordered with the `dagster/priority` tag and how queue priority interacts with run concurrency limits. It was used to reason about prioritizing fast-lane jobs over lower-priority backfills in the SRDP ETL deployment.

## Infrastructure and Deployment

- **[OpenTofu Documentation](https://opentofu.org/docs/)**
    - OpenTofu is used as the Infrastructure-as-Code tool for provisioning Scaleway resources (VPC, private network, Kapsule cluster, security group). It serves as an open-source alternative to Terraform.

- **[Scaleway Kubernetes Documentation](https://www.scaleway.com/en/docs/containers/kubernetes/)**
    - The Scaleway managed Kubernetes (Kapsule) documentation was referenced for configuring the production cluster, including the mutualized control plane, Cilium CNI, and autoscaling node pools in the nl-ams region.
