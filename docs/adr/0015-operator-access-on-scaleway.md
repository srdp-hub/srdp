---
status: proposed
date: 2026-10-28
decision-makers: Thomas Start
---

# Operator access on Scaleway

## Context and Problem Statement

Operators need `kubectl` and the operator tools of [ADR-0014](./0014-ingress-on-scaleway.md) in every environment.
Kapsule has no private API endpoint.
The API always has a public address, and Scaleway's default ACL allows `0.0.0.0/0`.
So the network controls for the API are an ACL and a controlled source IP.

Zitadel runs inside the cluster.
If operator access depended on Zitadel, a broken cluster would also lock the operators out of the tools they need to repair it.

Issue #40 discusses the access model and is where the team decides.
This ADR records the proposal that the #44 work builds on.

## Considered Options

1. A Headscale mesh for operators, with an API ACL that allows the gateway IP and one break-glass `/32`.
2. An API ACL with the operators' own IPs, and no mesh.
3. The SSH bastion on the Public Gateway.
4. Scaleway Site-to-Site VPN.
5. Pangolin, as proposed in #40.
6. The Headscale mesh with login through Zitadel.

## Decision Outcome

Chosen option: "A Headscale mesh for operators", because it gives operators one controlled path to the API and to the private load balancer, it keeps working when the cluster is broken, and end users never need it.

### Headscale in the hub

- Headscale runs on one VM in the hub with a public IP, a Let's Encrypt certificate, a built-in DERP relay with STUN on UDP port 3478, and a SQLite database.
- The ACL policy denies everything by default.
  It lets operators reach the environment's network range and the API `/32` on port 443, and nothing else.
- `tagOwners` and `autoApprovers.routes` map `tag:router-<env>` to the network range and the API `/32`, so a recreated router approves its own routes.
- A tagged router is not a member, so it cannot start connections into the network itself.
- MagicDNS stays on with the base domain `mesh.srdp.internal`, and `extra_records` point the operator hostnames under `ops.<domain>` at the private load balancer.
- Operators and routers join with pre-auth keys from Secret Manager.
  Each operator gets a key of their own that expires.
  Zitadel is not involved.

### A mesh router in each spoke

- The router is a small VM on the private network without a public IP.
- It forwards IP traffic and advertises the network range and the Kapsule API `/32`.
- Tailscale's SNAT stays on, so the private network needs no route back to the mesh range.
- It uses the private network's own resolver and does not accept mesh DNS.

Operators run `tailscale set --accept-routes --accept-dns` after they join.
`tailscale up` resets the preferences it does not name, so the runbook uses `tailscale set`.

### The Kapsule API ACL

- The ACL replaces Scaleway's default rule.
- The Public Gateway's fixed IP covers the nodes and every operator on the mesh, because the router sends API traffic through the gateway.
- One operator `/32` is the break-glass entry for the first apply and for when Headscale or the router is down.
- A validation rejects `0.0.0.0/0` and any prefix shorter than `/24`.
- In dev the break-glass `/32` stays in the ACL.
  In production it is added only while it is in use.
- Every request still needs the kubeconfig token.
  OpenTofu exposes it as a sensitive output, and it never goes into Git.
- Kapsule's audit logs in Cockpit record who used the API.

### CI and infrastructure changes

CI has no Scaleway credentials.
It only runs checks that need no cloud access, such as `tofu fmt`, `tofu validate` and the linters.
A leaked CI secret or a malicious pull request then cannot change the infrastructure or read the state.
The CI runner module in the blueprint stays unused.
Changes inside the cluster reach it through Flux, which pulls from Git, as [ADR-0016](./0016-gitops-with-flux.md) describes.
So CI needs no cluster credentials either.

In dev an operator runs `tofu plan` and `tofu apply` from their own machine, with the API key of that environment.
The S3 backend uses `use_lockfile`, so a second apply waits until the first one releases the lock in the state bucket.
This needs OpenTofu 1.10 or later, and the blueprint does not set it yet.
Locking stops two applies at the same time, but local applies keep two other problems as the team grows.
A plan is not visible in the pull request.
An operator can also apply a branch that never merges, and then the environment drifts from `main`.

Atlantis is the planned next step, and it must run before stage and production exist.
It runs `tofu plan` for every approved pull request that changes the OpenTofu code, posts the plan as a comment, and applies it before the merge.

The common pattern runs plan and apply in GitHub Actions with short-lived credentials.
The cloud issues them in exchange for the OIDC token that GitHub gives each job.
Scaleway IAM documents no way to trust that token, and its identity federation only covers single sign-on for people.
So the pipeline needs long-lived API keys, and those keys must stay on a machine that we control.
If Scaleway adds OIDC federation for workloads, we reconsider GitHub Actions with environment approvals.

The repository is public, and a plan runs code from the pull request, such as an external data source or a provider.
These rules keep the cloud keys away from GitHub and from code that nobody has reviewed.

- Atlantis runs on a VM in the hub. GitHub only sends it webhooks, signed with a shared secret, and holds no Scaleway key.
- The Atlantis VM has its own public IP, because GitHub must reach the webhook and the hub has no Public Gateway.
- A security group on that IP allows incoming traffic only on port 443, and only from the webhook address ranges that GitHub publishes.
- A reverse proxy on the VM passes only the webhook path from the public IP. The Atlantis UI shows plan output, so operators reach it only over the mesh.
- Atlantis ignores pull requests from forks, and only the members of one GitHub team can run Atlantis commands.
- Atlantis plans only approved pull requests, so no code runs with a cloud key before a reviewer has read it.
- Atlantis applies only pull requests that are approved, mergeable and undiverged. Undiverged means that `main` has not changed since the plan.
- The server config does not let a pull request change the workflow or these requirements.
- Each environment has a plan key and an apply key, and both keys only reach the Project of that environment.
- The plan key can read resources and write the lock file in the state bucket. The apply key can change resources.
- The plan key can still read the state, which holds the kubeconfig token, so the approval before the plan is the main control.
- Prod gets its own Atlantis VM in the hub, so a compromised dev pipeline cannot reach the prod keys.
- Atlantis reaches the Kapsule API over the mesh as a tagged node, so the API ACL does not change.

We also considered Flux's Tofu Controller, because it fits [ADR-0016](./0016-gitops-with-flux.md).
It runs inside the cluster, so it cannot repair or recreate the cluster that it runs in.
Atlantis runs in the hub, outside every spoke.

### Figure

[Operator access](/architecture-viewer/#/view/scaleway-operators/) shows the two operator paths.
On the mesh path, the laptop joins Headscale in the hub and sends traffic over WireGuard to the mesh router.
The router forwards API traffic through the Public Gateway, whose IP is in the ACL.
It forwards browser traffic for the operator tools to the private load balancer and the private Traefik.
On the break-glass path, the laptop calls the Kapsule API directly from the one `/32` in the ACL.
Red marks Headscale, the router and the gateway as single points of failure.

### Consequences

- Good, because only the gateway IP and one `/32` reach the Kubernetes API.
- Good, because operator access survives a broken cluster, since Headscale and the router are VMs outside it.
- Good, because end users never need a mesh client.
- Good, because one mesh serves `kubectl` and the operator tools in the browser.
- Good, because CI holds no cloud or cluster credentials, so a compromised workflow cannot change an environment.
- Bad, because the API endpoint stays public, and only the ACL and the token protect it.
- Bad, because Headscale, the mesh router and the Public Gateway are single instances. The break-glass `/32` with `kubectl port-forward` covers their failure in dev.
- Bad, because pre-auth keys are created and handed out by hand, and an operator who leaves needs their node removed in Headscale.
- Bad, because the Headscale VM costs about €10 per month and the router VM adds a small amount on top.
- Bad, because until Atlantis runs, every infrastructure change waits for an operator with the key, and a `tofu plan` only reaches a PR when the operator pastes it.
- Bad, because Atlantis will hold long-lived API keys, so an attacker on an Atlantis VM can change every environment that the VM serves.
- Bad, because the plan only runs after an approval, so a reviewer reads the code before they see its plan.

### Production hardening

- Two mesh routers per spoke that advertise the same routes, if the Headscale version supports route failover.
- Headscale data on a separate volume, with backups, monitoring and a rebuild runbook.
- Expiry and rotation for every pre-auth key.
- A break-glass `/32` that is only present while it is in use.
- Atlantis in the hub plans and applies every infrastructure change. A local `tofu apply` is then only for break-glass.
- The Atlantis API keys are rotated on a schedule. This stops when Scaleway supports OIDC federation for workloads.

## Pros and Cons of the Options

### An API ACL with the operators' own IPs, and no mesh

- Good, because it needs no VMs.
- Bad, because home and mobile IPs change, so the ACL needs edits all the time.
- Bad, because the operator tools behind the private load balancer stay unreachable.

### The SSH bastion on the Public Gateway

- Good, because Scaleway runs it, so it needs no VM.
- Bad, because it accepts SSH from any IP until `allowed_ip_ranges` is set.
- Bad, because it imports the Project's SSH keys only once, so later keys need a manual reimport.
- Bad, because forwarding to the private load balancer is not verified.

### Scaleway Site-to-Site VPN

- Good, because Scaleway manages it.
- Bad, because it connects networks, so it does not serve a laptop.
- Bad, because it costs about €59 per month.

### Pangolin

- Neutral, because #40 still discusses it. This ADR follows the outcome of #40.

### The Headscale mesh with login through Zitadel

- Good, because operators use the same identity as in the apps.
- Bad, because a broken cluster takes Zitadel down and locks the operators out of the mesh.

## Not verified

- Whether the API ACL needs `scaleway_ranges` for Kapsule's own components.
- Whether the Headscale version in the blueprint supports failover between two subnet routers.
- How long existing mesh connections keep working while Headscale is down.
- Whether Scaleway Object Storage supports the conditional writes that `use_lockfile` needs.
- Whether Scaleway IAM supports OIDC federation for workloads. We found no documentation of it.
- Whether a bucket policy can limit the plan key to the lock file in the state bucket.
- Whether the undiverged requirement works with the checkout strategy that the Atlantis config uses.
- How the security group follows changes to the webhook address ranges that GitHub publishes.

## Related

#40 (access model, where this is decided), #43 (pre-auth keys in Secret Manager, split DNS), #44 (combine the two Scaleway setups), [ADR-0002](./0002-api-and-access-strategy.md) (zero trust), [ADR-0013](./0013-scaleway-network-topology.md) (topology), [ADR-0014](./0014-ingress-on-scaleway.md) (ingress), [ADR-0016](./0016-gitops-with-flux.md) (GitOps with Flux).
