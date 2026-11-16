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
A request to the public load balancer IP with the `Host` header of an operator hostname would still match the operator route.

Issue #37 demos the read-only Dagster and the DuckDB UI to people outside the team, so these two stay public behind the login.

## Considered Options

1. One Traefik with three entrypoints, a public load balancer and a private load balancer.
2. Two Traefik deployments, one for public routes and one for operator routes.
3. Everything on the public load balancer behind the login.
4. Operator tools only through `kubectl port-forward`.

## Decision Outcome

Chosen option: "Two Traefik deployments", because a route stays private unless it asks to be public, and the public Traefik does not know the operator routes at all.

### Two Traefik releases

The SRDP chart installs the Traefik subchart twice, under two aliases.

- The private Traefik keeps the name `srdp-traefik` and the IngressClass `srdp-traefik`. This IngressClass is the default of the cluster.
- The public Traefik gets the name `srdp-traefik-public` and the IngressClass `srdp-traefik-public`.

Each release has one Kubernetes Service.
The public Service becomes the public load balancer, with the entrypoints `web` and `websecure`.
`web` takes port 80 and only redirects to https.
The private Service carries the Scaleway annotations for a private load balancer on the environment's private network.
It exposes only `websecure`.

### Rules that keep the two sides apart

- A route without an IngressClass gets the private class, so a forgotten setting hides a route instead of exposing it.
- Every public route sets `ingressClassName: srdp-traefik-public` explicitly.
- Each Traefik reads only the routes of its own class. This applies to Ingress objects and to Traefik's `IngressRoute` objects, such as the Zitadel route.
- Zitadel and the `/oauth2` routes of the public hostnames sit on the public Traefik, because the login must work from the internet.
- The `/oauth2` routes of the operator hostnames sit on the private Traefik, because each hostname needs its own callback path.
- A NetworkPolicy denies all incoming traffic in the namespace by default. Allow rules let the public Traefik reach only the public backends.
- Only the private Traefik can reach the operator backends.
- The operator hostnames sit under `ops.<domain>`, such as `dagster.ops.<domain>`.
- Public DNS has explicit records for the public hostnames only, and no wildcard record.
- Headscale's DNS serves the operator hostnames to the mesh, as [ADR-0015](./0015-operator-access-on-scaleway.md) describes.

### Tests

Two tests prove that routes are private by default.

1. CI renders the chart and fails when a route uses the public class but is not on the list of public routes. The same test fails when an `IngressRoute` has no class.
2. `curl --resolve dagster.ops.<domain>:443:<public LB IP> https://dagster.ops.<domain>/` must return 404.

The first test runs on every pull request without a cluster.
The second test runs against a deployed environment and shows that the public Traefik does not know the route.

### What sits where

| Side | Routes | Role |
|:---|:---|:---|
| Public | Hub page, Zitadel, marimo, Streamlit, the API | `user` |
| Public | Read-only Dagster, DuckDB UI | `demo` |
| Private | Read-write Dagster at `dagster.ops.<domain>`, and later operator tools | `admin` |

The DuckDB UI is a public SQL console, and today it can write.
It reads the lakehouse files with the S3 reader key, so it cannot change the files.
It connects to the DuckLake catalog as the `postgres` superuser, so a user can change or delete the catalog and can reach the other databases on the same server.
That makes it more dangerous than the read-only Dagster.
It stays public for the #37 demo on sample data only.
#132 gives the apps a reader role that can only read the catalog, so no app gets the superuser password.
It moves to the private Traefik, or gets scoped credentials as [ADR-0009](./0009-data-plane-credential-materialization.md) requires, before any customer data arrives.

### Domain and certificates

Each deployment sets its own domain in `global.domain`.
SRDP's own dev and demo environments use subdomains of `srdp.dev`, such as `dev.srdp.dev`, which #37 proposes.
A customer deployment uses a subdomain of the customer's own domain, such as `data.customer.example`.
The owner of the parent zone delegates the subdomain to Scaleway Domains and DNS in the deployment's Project, and OpenTofu manages that zone.
Let's Encrypt checks the DNS-01 challenge in the delegated zone, so each deployment gets certificates for its own names without access to the parent zone.

Each Traefik has its own ACME resolver and gets its own wildcard certificate from Let's Encrypt over DNS-01.

- The public Traefik gets `<domain>` and `*.<domain>`.
- The private Traefik gets `*.ops.<domain>`.

A wildcard covers one level of names, so the public certificate does not cover the operator hostnames.
DNS-01 is needed because the operator hostnames resolve only on the mesh, and Let's Encrypt cannot reach a private load balancer over HTTP-01.
Each Traefik proves ownership of the zone with a DNS key that can only change this zone.
The keys reach the cluster from Secret Manager through External Secrets.
cert-manager could issue one shared certificate instead, but SRDP does not run cert-manager today.

### Login without loops

The forward-auth middleware only sits on app routes.
One oauth2-proxy checks the requests from both Traefiks.
Three groups of routes bypass it, so the login can never send a request back into itself.

- `/oauth2/*` on every hostname, with the sign-in page, the callback and the sign-out.
- Everything on `auth.<domain>`, which is Zitadel and its login UI.
- oauth2-proxy's own error page.

Three more rules prevent login failures.

- The `oauth-errors` middleware only catches 401. A signed-in user without the role gets a 403 page and is not sent back to the sign-in page, because that would loop.
- The setup service registers `https://<hostname>/oauth2/callback` for every public and operator hostname. The cookie domain `.<domain>` makes one login valid on all of them, including the hostnames under `ops.<domain>`.
- oauth2-proxy resolves `auth.<domain>` to the public Traefik's ClusterIP with `hostAliases`. The issuer check then stays inside the cluster, with TLS and issuer verification on.

### When the login fails

Every browser path goes through Zitadel, also for operators.
A broken or misconfigured Zitadel therefore locks the operators out of the operator tools.
Access to the infrastructure does not depend on Zitadel.
`kubectl` uses the Kapsule kubeconfig, and the mesh uses Headscale pre-auth keys.
When the login fails, an operator reaches a tool with `kubectl port-forward` over the mesh, which skips Traefik and the login.

Three rules keep Zitadel itself recoverable.

- At least two human admins exist, and both use MFA.
- The setup service works with a Zitadel machine user and assigns the admin role from config on every deploy. The next deploy then undoes a wrong role change.
- The password lockout stays off, because the login page is public and anyone could lock an admin account. MFA and a rate limit on the login paths in the public Traefik protect against password guessing.

A separate issue covers the recovery of Zitadel itself, such as a changed master key or a restore from backup.

### Compose and kind

kind runs both Traefik releases from the same chart, so the split and its CI test also work locally.
Compose keeps one Traefik with `web` and `websecure`.
A local machine is one trust zone, so a private entry adds no protection there.
This is a documented difference between the two deployment targets.

### Figure

[Ingress and login](/architecture-viewer/#/view/scaleway-ingress/) shows the two entry paths and the two Traefiks.
The end user comes in through the public load balancer to the public Traefik.
The operator comes in through the mesh router and the private load balancer to the private Traefik.
Inside the namespace, both Traefiks ask oauth2-proxy to check each app request, and oauth2-proxy checks the issuer through the public Traefik's ClusterIP.
The read-write and the read-only Dagster are two instances of the same webserver, and only the private Traefik routes to the read-write one.

### Consequences

- Good, because a route without an explicit class lands on the private side, so a mistake hides a route instead of exposing it.
- Good, because the public Traefik has no configuration for the operator routes, so a forged `Host` header returns 404.
- Good, because the NetworkPolicy blocks the public Traefik from the operator backends, even if a route is misconfigured or the public Traefik is compromised.
- Good, because the public certificate does not cover the operator hostnames.
- Good, because the CI test catches an exposed route before it is deployed.
- Good, because end users need no mesh client, and the demo of #37 works from any browser.
- Good, because the operator tools work in the browser with the same login as the apps.
- Bad, because the chart must run, configure and upgrade two Traefik releases with two IngressClasses and two ACME resolvers.
- Bad, because `AGENTS.md` and `deploy.instructions.md` name one IngressClass, and they must change when the chart changes.
- Bad, because Compose has one Traefik and does not match the Kubernetes target.
- Bad, because the private load balancer costs about €17 per month.
- Bad, because each DNS key can get certificates for every name in the zone, so the keys must stay scoped to that zone and live only in Secret Manager.
- Bad, because operators also log in at the public `auth.<domain>`, so a broken Zitadel locks them out of the operator tools. `kubectl port-forward` is the way in until the login works again.
- Bad, because the DuckDB UI is a public SQL console with superuser access to the catalog until #132 and the move to the private Traefik are done.

## Pros and Cons of the Options

### One Traefik with three entrypoints

- Good, because one Traefik, one certificate and one set of middlewares serve both sides.
- Good, because one `curl` test can check the separation.
- Bad, because a missing entrypoint annotation exposes an operator route on the public side.
- Bad, because the public Traefik knows the operator routes, so only the entrypoint annotation keeps them apart.
- Bad, because a Traefik vulnerability or a configuration mistake affects both sides.

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
- Whether Zitadel ships with the password lockout off.
- Whether Scaleway's private load balancer accepts the annotations above with Kapsule's cloud controller in `nl-ams`.
- Whether Kapsule's default CNI, Cilium, enforces NetworkPolicy without extra settings.
- Whether two aliases of the Traefik subchart install cleanly in one release, because both ship the same CRDs.
- Whether Traefik's CRD provider filters `Middleware` objects by class. If it does, both Traefiks need their own copy of the forward-auth middleware.
- Whether a Scaleway DNS key can be limited to `ops.<domain>`, so the private Traefik cannot change the public records.
- How kind exposes the private Traefik next to the public one on the host ports.
- Whether a Compose host on the internet, with `docker-compose.prod.yml`, needs a private entry too. Today it routes Dagster on the public side.

## Related

#37 (demo on Scaleway), #40 (access model), #132 (catalog roles instead of the superuser), [ADR-0002](./0002-api-and-access-strategy.md) (Traefik and Zitadel at the edge), [ADR-0005](./0005-authorization-and-data-access.md) (role per route, read-only Dagster), [ADR-0008](./0008-identity-propagation-and-data-contracts.md) (the identity headers are hints), [ADR-0009](./0009-data-plane-credential-materialization.md) (scoped credentials), [ADR-0013](./0013-scaleway-network-topology.md) (topology), [ADR-0015](./0015-operator-access-on-scaleway.md) (operator access).
