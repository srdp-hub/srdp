---
status: proposed
date: 2026-10-28
decision-makers: Thomas Start
---

# Ingress on Scaleway: a public and a private entry

## Context and Problem Statement

[ADR-0002](./0002-api-and-access-strategy.md) puts Traefik, Zitadel and oauth2-proxy at the edge.
[ADR-0005](./0005-authorization-and-data-access.md) checks a role per route and makes the exposed Dagster UI read-only.
The read-write Dagster webserver can start runs and wipe assets.
It is an operator tool, and it must not be reachable from the internet.

Today no Ingress in the chart sets a Traefik entrypoint, so Traefik attaches every route to every entrypoint.
DNS alone does not hide a route.
A request to the public load balancer IP with `Host: dagster-admin.<domain>` would still match the operator route.

Issue #37 demos the read-only Dagster and the DuckDB UI to people outside the team, so these two stay public behind the login.

## Considered Options

1. One Traefik with three entrypoints, a public load balancer and a private load balancer.
2. Two Traefik deployments, one for public routes and one for operator routes.
3. Everything on the public load balancer behind the login.
4. Operator tools only through `kubectl port-forward`.

## Decision Outcome

Chosen option: "One Traefik with a public and a private load balancer", because it keeps one Traefik configuration and one certificate, and the separation is a rule that a single test can check.

### Entrypoints and load balancers

Traefik has three entrypoints.

- `web` takes port 80 and only redirects to https.
- `websecure` takes port 443 for the public routes.
- `admin` takes port 443 for the operator routes.

Traefik gets two Kubernetes Services.
The default Service becomes the public load balancer and exposes only `web` and `websecure`.
An extra Service becomes the private load balancer and exposes only `admin`.
The extra Service carries the Scaleway annotations for a private load balancer on the environment's private network.
Traefik chart 37.4.0, which the SRDP chart uses, supports this through `service.additionalServices` and `ports.<entrypoint>.expose`.

### Rules that keep the two sides apart

- `web` and `websecure` are the default entrypoints, so a route without an explicit entrypoint never lands on `admin`.
- Every operator route sets `traefik.ingress.kubernetes.io/router.entrypoints: admin`, so it never lands on a public entrypoint.
- The `/oauth2` routes of the operator hostnames also sit on `admin`, because each hostname needs its own callback path.
- Zitadel's route stays on `websecure` only.
- Public DNS has explicit records for the public hostnames only, and no wildcard record.
- Headscale's DNS serves the operator hostnames to the mesh, as [ADR-0015](./0015-operator-access-on-scaleway.md) describes.

The test is `curl --resolve dagster-admin.<domain>:443:<public LB IP> https://dagster-admin.<domain>/`.
It must return 404.

### What sits where

| Side | Routes | Role |
|:---|:---|:---|
| Public | Hub page, Zitadel, marimo, Streamlit, the API | `user` |
| Public | Read-only Dagster, DuckDB UI | `demo` |
| Private | Read-write Dagster at `dagster-admin.<domain>`, and later operator tools | `admin` |

The DuckDB UI is a SQL console with a long-lived storage key.
It stays public for the #37 demo on sample data only.
It moves behind the private load balancer, or gets scoped credentials as [ADR-0009](./0009-data-plane-credential-materialization.md) requires, before any customer data arrives.

### Domain and certificate

Each environment gets a subdomain of `srdp.dev`, which #37 proposes, such as `dev.srdp.dev`.
The parent zone delegates the subdomain to Scaleway Domains and DNS, and OpenTofu manages the zone in the environment's Project.
Traefik gets one wildcard certificate for `<domain>` and `*.<domain>` from Let's Encrypt over DNS-01.
DNS-01 is needed because the operator hostnames resolve only on the mesh, and Let's Encrypt cannot reach a private load balancer over HTTP-01.
The one certificate then covers the public and the operator hostnames.
Traefik proves ownership of the zone with a DNS key that can only change this zone.
The key reaches the cluster from Secret Manager through External Secrets.

### Login without loops

The forward-auth middleware only sits on app routes.
Three groups of routes bypass it, so the login can never send a request back into itself.

- `/oauth2/*` on every hostname, with the sign-in page, the callback and the sign-out.
- Everything on `auth.<domain>`, which is Zitadel and its login UI.
- oauth2-proxy's own error page.

Three more rules prevent login failures.

- The `oauth-errors` middleware only catches 401. A signed-in user without the role gets a 403 page and is not sent back to the sign-in page, because that would loop.
- The setup service registers `https://<hostname>/oauth2/callback` for every public and operator hostname. The cookie domain `.<domain>` makes one login valid on all of them.
- oauth2-proxy resolves `auth.<domain>` to Traefik's ClusterIP with `hostAliases`. The issuer check then stays inside the cluster, with TLS and issuer verification on.

### Figure

[Ingress and login](/architecture-viewer/#/view/scaleway-ingress/) shows the two entry paths into the one Traefik.
The end user comes in through the public load balancer on `web` and `websecure`.
The operator comes in through the mesh router and the private load balancer on `admin`.
Inside the namespace, Traefik asks oauth2-proxy to check each app request, and oauth2-proxy checks the issuer through Traefik's ClusterIP.
The read-write and the read-only Dagster are two instances of the same webserver, and only the read-write one is on `admin`.

### Consequences

- Good, because one Traefik, one certificate and one set of middlewares serve both sides.
- Good, because the separation is testable with one `curl` command, which can run in CI against a deployed environment.
- Good, because end users need no mesh client, and the demo of #37 works from any browser.
- Good, because the operator tools work in the browser with the same login as the apps.
- Bad, because a route that forgets its entrypoint annotation lands on the public side. The default entrypoints keep that to the public side only, and the test catches a forged `Host` header.
- Bad, because the private load balancer costs about €17 per month.
- Bad, because the DNS key can get certificates for every name in the zone, so it must stay scoped to that zone and live only in Secret Manager.
- Bad, because operators on the mesh still log in at the public `auth.<domain>`.
- Bad, because the DuckDB UI is public with a long-lived storage key until it moves.
- Neutral, because a single Traefik process serves both sides, so a Traefik vulnerability affects both. Option 2 removes that if production needs it.

## Pros and Cons of the Options

### Two Traefik deployments

- Good, because the public and the operator routes run in separate processes with separate configuration.
- Bad, because the chart must run, configure and upgrade two Traefik releases with two IngressClasses.
- Bad, because the certificate and the middlewares exist twice.

### Everything on the public load balancer behind the login

- Good, because it is the simplest setup and costs no extra load balancer.
- Bad, because the read-write Dagster is reachable from the internet, and only the login protects it.
- Bad, because ADR-0005 makes the exposed Dagster UI read-only.

### Operator tools only through `kubectl port-forward`

- Good, because it needs no private load balancer.
- Bad, because every operator needs `kubectl` access to use a browser tool.
- Bad, because a port-forward skips the login and the role check.

## Not verified

- Whether the `oauth-errors` middleware loops on a 403 today. The design treats it as a loop until a test shows otherwise.
- Whether Scaleway's private load balancer accepts the annotations above with Kapsule's cloud controller in `nl-ams`.

## Related

#37 (demo on Scaleway), #40 (access model), [ADR-0002](./0002-api-and-access-strategy.md) (Traefik and Zitadel at the edge), [ADR-0005](./0005-authorization-and-data-access.md) (role per route, read-only Dagster), [ADR-0008](./0008-identity-propagation-and-data-contracts.md) (the identity headers are hints), [ADR-0009](./0009-data-plane-credential-materialization.md) (scoped credentials), [ADR-0013](./0013-scaleway-network-topology.md) (topology), [ADR-0015](./0015-operator-access-on-scaleway.md) (operator access).
