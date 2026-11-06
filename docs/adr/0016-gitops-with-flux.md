---
status: proposed
date: 2026-10-30
decision-makers: Thomas Start
---

# GitOps with Flux for cloud environments

## Context and Problem Statement

The SRDP chart must reach the Kapsule cluster of every cloud environment.
Later, stage and prod must run a chart version that dev has already proven.

The old Scaleway stack ran `helm upgrade` from an operator's machine through the `prod-*` recipes.
With that approach, Git does not show which chart version and which values run in an environment.

[ADR-0015](./0015-operator-access-on-scaleway.md) gives CI no cloud credentials, so CI cannot push a deploy either.
Secrets never live in Git, as #61 (ADR-0010) decides.
Andrea's blueprint already installs Flux from OpenTofu, and #38 agreed on the `deploy/gitops/` layout.
This ADR records how the cloud environments deploy the chart.
Compose and kind are outside its scope.

## Considered Options

1. Flux in each cluster, which pulls the environment's leaf from Git and installs the chart as OCI from the hub registry with a pinned version.
2. `helm upgrade` from an operator's machine through `just` recipes, as the old stack did.
3. A CI pipeline that runs `helm upgrade` after each merge.
4. Argo CD in each cluster.

## Decision Outcome

Chosen option: "Flux in each cluster", because Git then shows what runs in every environment, a deploy needs no credentials outside the cluster, and a chart version moves from dev to stage by changing one pin.

### Flux comes with the cluster

OpenTofu installs Flux in each spoke through `spoke_k8s` and the `flux` module.
Flux reconciles exactly one path, which is the environment's leaf `deploy/gitops/clusters/<cloud>/<env>`.
A `tofu apply` therefore leaves a cluster that already deploys from Git, without a separate `flux bootstrap`.
The `spoke` module creates the cluster and `spoke_k8s` installs Flux, so the Kubernetes providers only plan against a cluster that already exists.

### The GitOps tree

The tree follows the layout that #38 agreed on.

- `platform/base` holds the cloud-agnostic controllers, such as External Secrets.
- `srdp/base` holds the HelmRelease for the SRDP chart and the values that all environments share.
- `clusters/<cloud>/<env>` is the leaf of one cluster. It holds the ClusterSecretStore, the ExternalSecrets, the values of that environment and its chart version pin.

No leaf refers to another leaf, so each environment upgrades on its own.
#86 may rename `deploy/` by function.
The structure in this section stays the same after such a rename.

### The chart and the images

- The chart is published as OCI to the hub registry under its chart version.
- Each leaf pins one chart version, so a chart change reaches dev first, and stage and prod only move when their own pin changes.
- The four project images go to the hub registry with the commit hash as their tag.
- The platform images come from ghcr.io, where CI already publishes them.
- An ExternalSecret builds the registry pull secret from the registry key in Secret Manager.

### Secrets

Git holds only the names of Secrets and the references into Secret Manager.
External Secrets reads Secret Manager with the key that OpenTofu puts in the cluster.
It creates the Secrets with the fixed names that the chart reads.

### OpenTofu outputs

Some chart values come from OpenTofu outputs, such as the database host and the bucket names.
For now, a table in the runbook says which output fills which value, and an operator copies the values into the leaf.
#38 proposed that OpenTofu writes the outputs into a ConfigMap that the HelmRelease reads through `valuesFrom`.
That handoff stays the preferred next step.

### Which branch Flux reads

Dev reads `main`, so a merge that changes `deploy/gitops/` is a deploy to dev.
Such a PR says how to roll back, which is a revert of the PR.
To test a change before the merge, an operator can point dev's Flux at the PR branch with the `git_branch` variable, and point it back after the check.
Stage and prod get their own leaf later, and their chart version only changes through a PR that changes their pin.

### Local setups

kind installs the chart with `just local-deploy`, so the chart stays testable without a registry or Flux.
Compose does not use the chart.

### Consequences

- Good, because Git shows the chart version and the values of every environment.
- Good, because a deploy needs no cloud or cluster credentials outside the cluster, which matches the CI rule in ADR-0015.
- Good, because a rollback is a revert in Git, and a promotion to stage changes one pin.
- Good, because Flux restores drift, so a manual change in the cluster does not last unnoticed.
- Bad, because a merge to `main` deploys to dev at once, so a broken GitOps change breaks dev until someone reverts it.
- Bad, because a failed reconcile only shows up in the cluster, so the author must check `flux get` after the merge.
- Bad, because Flux reverts a manual hotfix in the cluster, unless the operator suspends the Kustomization first.
- Bad, because Flux, its controllers and External Secrets are more components to pin and upgrade.
- Bad, because an operator copies the OpenTofu outputs into the leaf by hand until the ConfigMap handoff exists.
- Neutral, because kind and the cloud install the same chart through different paths, so a problem in the Flux layer can show up only in the cloud.

## Pros and Cons of the Options

### `helm upgrade` from an operator's machine

- Good, because it needs no extra components in the cluster, and kind already works this way.
- Bad, because Git does not show what runs in an environment.
- Bad, because two operators can overwrite each other's deploys.
- Bad, because every deploy needs cluster credentials on a laptop.

### A CI pipeline that runs `helm upgrade`

- Good, because every deploy follows a merge and leaves a log.
- Bad, because CI then holds cluster credentials, which ADR-0015 rules out.
- Bad, because the CI runner needs a path to the Kubernetes API, so the API ACL must allow it.

### Argo CD

- Good, because its web UI shows the sync state of every app.
- Bad, because that UI is one more operator tool that needs a login and a place behind the private load balancer.
- Bad, because the blueprint already installs Flux from OpenTofu, and the Azure design that the blueprint was ported from also uses Flux.

## Not verified

- Whether the pinned `flux2` chart reads OCI charts from the Scaleway registry with the pull secret from External Secrets.
- In which order Flux and External Secrets come up on a fresh cluster, and whether the HelmRelease waits for its Secrets.

## Related

#38 (shared deployment layer), #44 (combine the two Scaleway setups), #61 (secrets out of Git, ADR-0010), #86 (plugin structure for deployment providers), [ADR-0013](./0013-scaleway-network-topology.md) (topology and the hub registry), [ADR-0015](./0015-operator-access-on-scaleway.md) (operator access and CI), [Kubernetes primer](../10-kubernetes-primer.md).
