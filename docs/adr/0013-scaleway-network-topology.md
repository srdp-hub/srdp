---
status: proposed
date: 2026-10-28
decision-makers: Thomas Start
---

# Network topology on Scaleway

## Context and Problem Statement

SRDP had two Scaleway setups.
The old single-file OpenTofu stack in `deploy/opentofu/scaleway` put every environment in one Scaleway Project and gave the nodes public IPs.
The hub-and-spoke blueprint in `deploy/scaleway` separates shared parts from environments and keeps the nodes private.
Issue #44 asks which setup stays and what to keep from the other one.

[ADR-0006](./0006-deployment-and-project-isolation-model.md) makes the deployment the isolation unit.
On a cloud provider, that needs a network boundary per deployment that the provider enforces.
Each environment also needs a controlled way out to the internet, because the nodes pull images, request certificates and call Scaleway APIs.

The first environment is dev.
Dev is identical to the stage design, so one environment proves every concept before stage and prod copy it.

## Considered Options

1. Hub and spokes, with one Scaleway Project per environment, private nodes and one Public Gateway per environment.
2. One Project for all environments with public nodes, as the old stack did.
3. One Project per environment without a hub, so each environment runs its own registry and its own mesh server.

## Decision Outcome

Chosen option: "Hub and spokes", because a Project per environment gives a boundary that Scaleway enforces for networks, IAM and billing, and the hub keeps the shared parts in one place.

### The hub

The hub is one Scaleway Project with the parts that every environment shares.

- The Container Registry holds the project images and the OCI chart, which Flux installs as [ADR-0016](./0016-gitops-with-flux.md) describes.
- Headscale runs on one small VM and coordinates the operator mesh, as [ADR-0015](./0015-operator-access-on-scaleway.md) describes.

VPC peering connects two VPCs, so that resources on their private networks can reach each other without the internet.
The hub and the spokes are not peered, because the hub runs no private service that a spoke needs, and peering would make the hub a network path into every spoke, which weakens the isolation boundary of ADR-0006.
The blueprint can turn on peering later for each spoke with `enable_hub_peering`, because the address ranges of the hub and the spokes do not overlap.
A spoke reaches the registry and Headscale on their public endpoints, through its Public Gateway.
A second customer gets its own hub, so no hub is shared across the isolation boundary of ADR-0006.

### A spoke for each environment

Each environment is one Scaleway Project with its own OpenTofu state bucket and its own API key.

- A private network holds the Kapsule nodes, the mesh router, the private load balancer and the managed database.
- No node has a public IP.
- The managed database has no public endpoint, and each app connects with its own role over TLS.
- The Public Gateway gives the private network NAT and one fixed outbound IP.
- The public load balancer is the only inbound path for end users, as [ADR-0014](./0014-ingress-on-scaleway.md) describes.
- Object Storage, Secret Manager, the DNS zone and Cockpit are managed services in the same Project.

### Outbound traffic

All outbound traffic leaves through the Public Gateway.
That covers images from ghcr.io and the hub registry, the certificate from Let's Encrypt, the lakehouse buckets on the public S3 endpoint, Secret Manager and the Kapsule API.
One fixed IP makes three other controls possible.
The Kapsule API ACL allows that IP instead of every node, and an egress allowlist (#180) can later restrict where the traffic goes.
A customer can also allow that one IP in its own firewall, so that Dagster can read from a source system behind that firewall.

Object Storage private access would keep lakehouse traffic on the private network.
It is in private beta in `fr-par` only, so the buckets use the public endpoint until it reaches `nl-ams`.

### Where Postgres runs

Zitadel, Dagster, Marquez and the DuckLake catalog keep their state in Postgres.
That state includes the users, the run history and the table metadata, and the platform cannot rebuild it from Git.
We considered two places for Postgres in a spoke.

1. An in-cluster Postgres, which kind uses today.
2. Scaleway's managed database on the spoke's private network.

Each spoke uses the managed database.
Issue #65 showed the risk of the in-cluster option.
On a fresh install the subchart's default memory limit of 192 MiB stopped Postgres during startup, and Zitadel and both Dagster webservers failed with it.
A larger limit fixes that one failure, but the database still shares nodes, memory and node pool changes with the apps that it serves.
#65 concluded that Postgres must not run inside the application cluster.
For kind, #123 replaces the Bitnami subchart with an SRDP template that runs the same Postgres image as Compose.

The blueprint's `postgres` module gives the managed database these properties.

- It has only a private endpoint on the spoke's private network, so no database traffic passes the Public Gateway.
- Scaleway makes automatic backups and encrypts the volume at rest.
- One setting turns on high availability for production.
- The managed database only creates an admin user, so the setup Job creates one role per app (#132), and no workload gets the admin credentials.
- Each app connects with its own role over TLS.

In the cloud the chart turns the in-cluster Postgres off and points `global.postgresqlHost` at the private hostname of the managed database.
Compose and kind keep a Postgres container next to the apps, because they are local setups without managed services.

Dev also uses the managed database, because dev is identical to the stage design.
The private connection, TLS and the roles are then proven in dev before stage and prod copy them.

The managed database costs about €12 to €15 per month with storage, and it keeps running when the node pools are at zero.
The in-cluster option costs nothing extra, but it would bring the failure of #65 to every environment.

### Figures

The views are in the LikeC4 model, in the folder "3. Deployment / Scaleway networking (draft)".
Red marks a single point of failure.

- [Overview](/architecture-viewer/#/view/scaleway-overview/) shows the hub Project, the dev Project with its private network, the end user, the operator laptop and the internet.
  It shows which parts have a public address.
  These are the public load balancer, the Kapsule API, the Public Gateway and Headscale.
- [Outbound traffic](/architecture-viewer/#/view/scaleway-outbound/) shows that every outbound path from the cluster passes the Public Gateway.
  It also shows why the gateway is a single point of failure.
  When it stops, the nodes lose their images, their certificates, their data and their path to the control plane.

### Consequences

- Good, because Scaleway enforces the boundary between environments for networks, IAM and billing.
- Good, because the nodes accept no inbound traffic from the internet.
- Good, because one fixed outbound IP makes the API ACL small and makes an egress allowlist possible later.
- Good, because a separate state bucket and API key per environment means a mistake in dev cannot change prod.
- Good, because dev is identical to stage, so every concept is proven before it reaches customer data.
- Good, because Postgres runs outside the cluster, so a node failure or a memory limit in the cluster cannot stop the platform state.
- Bad, because the Public Gateway is a single point of failure for all outbound traffic, for the API path and for the lakehouse data.
- Bad, because the gateway, the load balancers and the database keep running when the node pools are at zero, so switching dev off saves only about €20 to €40 per month.
- Bad, because the lakehouse traffic leaves the private network until Object Storage private access is available in `nl-ams`.
- Bad, because a compromise of the hub registry reaches every environment that pulls from it.
- Bad, because the cloud database differs from the Postgres container in Compose and kind, so a problem with TLS or roles can show up only in the cloud.
- Bad, because OpenTofu writes the generated passwords and the kubeconfig into the state, so each state bucket is as sensitive as Secret Manager and must stay private and versioned.
- Bad, because Kapsule runs several nodes, so the rule in [ADR-0002](./0002-api-and-access-strategy.md) applies, and internal service-to-service calls must authenticate with Zitadel service accounts. This is a follow-up after dev proves every concept.

### Production hardening

Production adds these items on top of the dev design.

- A dedicated Kapsule control plane with an SLA.
- The managed database in high-availability mode, with a tested restore.
- At least two nodes in the system pool.
- Monitoring and a recreate runbook for the Public Gateway that keeps its IP.
- The egress allowlist from #180.
- Object Storage private access once it is available in `nl-ams`.
- VPC peering between the hub and each spoke, when the hub gets a private service that the spokes need, such as a registry mirror. The trade-off is that the hub becomes a network path into every spoke, which weakens the ADR-0006 boundary.

### Customer data

No environment gets customer data before seven conditions are met.
Until then, dev and the demo run on CBS sample data only.

1. The mesh runs in that environment.
2. The managed database runs in that environment.
3. Every hop validates the JWT, as [ADR-0008](./0008-identity-propagation-and-data-contracts.md) requires.
4. The project-scoped RBAC from [ADR-0005](./0005-authorization-and-data-access.md) exists.
5. No data-plane credential outlives a capability token, as [ADR-0009](./0009-data-plane-credential-materialization.md) requires. Today the static reader key in the DuckDB UI breaks this rule.
6. The baseline audit passes.
7. Egress filtering works (#180).

## Pros and Cons of the Options

### One Project for all environments with public nodes

- Good, because it is one OpenTofu root and the cheapest setup.
- Bad, because environments share IAM, so a dev key can change prod.
- Bad, because every node has a public IP and accepts inbound traffic that only a security group filters.
- Bad, because the outbound IPs change with the nodes, so the API ACL cannot name them.

### One Project per environment without a hub

- Good, because each environment stands completely on its own.
- Bad, because each environment pays for its own registry and its own Headscale VM.
- Bad, because an image or an operator key must be copied to every environment.

## Not verified

- Whether a replaced Public Gateway keeps its IP. The `egress-gateway` module creates the IP as a separate resource, so only an apply that replaces the gateway is left to test.
- Whether Scaleway offers a highly available Public Gateway type.
- When Object Storage private access comes to `nl-ams`, and what it costs.
- Whether the Container Registry has a private-network endpoint. The Scaleway documentation describes none.
- Whether VPC peering works between Projects. The blueprint records an `invalid argument(s)` error from the API for a connector between Projects.

## Related

#44 (combine the two Scaleway setups), #37 (demo on Scaleway), #43 (blueprint hardening), #180 (egress filtering), #65 and #123 (where Postgres runs), #132 (a role for DuckLake), [ADR-0006](./0006-deployment-and-project-isolation-model.md) (deployment as the isolation unit), [ADR-0014](./0014-ingress-on-scaleway.md) (ingress), [ADR-0015](./0015-operator-access-on-scaleway.md) (operator access), [ADR-0016](./0016-gitops-with-flux.md) (GitOps with Flux).
