---
title: 9. Kubernetes Primer
icon: lucide/ship-wheel
---

# Kubernetes primer for this deployment

This page explains the Kubernetes concepts you meet when you deploy the chart.
You already know Docker Compose, so each concept also says what it would be in Compose.
Read the whole page once before your first Kubernetes deploy, and use it as a reference afterwards.

## What Kubernetes is

Docker Compose starts containers on one machine, your laptop.
Kubernetes does the same on a group of machines, and keeps checking afterwards that everything still runs.
If a container crashes, Kubernetes starts it again.
If a machine disappears, Kubernetes moves the work to another machine.

You describe in YAML files what the situation should look like, for example "one Zitadel container runs with these settings".
Kubernetes continuously makes sure reality matches that description.
This is called the *desired state*.

## The building blocks

### Cluster and node

A **cluster** is the whole group of machines plus the brain that controls them.
A **node** is one machine in the cluster.
The brain is called the **control plane**, and it contains among other things the API server that receives your commands.

This does not exist in Compose, because there is only one machine.

### Pod

A **pod** is the smallest unit Kubernetes starts.
Usually a pod contains exactly one container.
You can think of a pod as one entry under `services:` in `docker-compose.yml` that is actually running.

Pods are disposable.
When a pod crashes or is moved, a new pod with a new name replaces it.

### Deployment

A **Deployment** describes which pods should run, with which image, which environment variables and how many copies.
It is the closest thing to a service block in `docker-compose.yml`.
When you change the image in a Deployment, Kubernetes gradually replaces the old pods with new ones.

### Service

Pods keep getting new IP addresses.
A **Service** gives a group of pods one fixed name and one fixed address inside the cluster.
In Compose you reach other containers by their service name, for example `db-postgresql`.
A Kubernetes Service does exactly that.

A Service of type `LoadBalancer` is special.
In the cloud it asks the provider for a real load balancer with a public IP address.
In our setup only Traefik gets such a Service.

### Ingress

An **Ingress** is a rule that says which web address goes to which Service, for example "`dagster.<domain>` goes to the Dagster webserver".
In our Compose stack those rules are Traefik labels on each service.
In Kubernetes they are separate Ingress objects, and Traefik reads them.

### PersistentVolumeClaim (PVC)

A pod loses its files when it restarts.
A **PVC** requests persistent storage that survives a pod restart.
It is the equivalent of a named volume under `volumes:` in Compose.

Locally a PVC is a folder on your laptop.
On Scaleway a PVC becomes a Block Storage volume, a virtual disk that stays around while the nodes are off.

### Secret and ConfigMap

A **Secret** holds sensitive values such as passwords.
A **ConfigMap** holds regular configuration.
Pods read both as environment variables or as files.
In Compose the closest thing is the `.env` file.

### Namespace

A **namespace** is a folder inside the cluster for grouping things.
Our stack runs in the `srdp` namespace.
Other components, such as Flux and External Secrets, get their own namespace.

## Helm

A Kubernetes stack quickly grows to dozens of YAML files.
**Helm** bundles them into a **chart**, a package of templates.
You fill the templates with **values**, and Helm turns them into the real YAML.
An installed chart is called a **release**.

Our chart lives in `deploy/kubernetes/srdp-chart/`.
It contains three kinds of files.

- `templates/` holds the templates, one file per app, such as `api.yaml` and `hub.yaml`.
- `values.yaml` holds the defaults.
- `values-local.yaml` holds the overrides for kind.

The chart also uses existing charts from others, such as the ones for Postgres, Zitadel, Traefik, oauth2-proxy and Dagster.
Those are listed as dependencies in `Chart.yaml`.

`helm template` shows the YAML Helm would produce without installing anything.
That is useful for finding mistakes.

## kind and Kapsule

**kind** (Kubernetes IN Docker) runs a real Kubernetes cluster on your laptop.
Each node is a Docker container.
**Kapsule** is Scaleway's managed Kubernetes, with real virtual machines as nodes.

The same chart works on both.
What sits underneath is different.

| | kind (local) | Kapsule (Scaleway) |
|:---|:---|:---|
| Nodes | Docker containers on your laptop | Virtual machines in a node pool |
| Control plane | Runs in a container | Managed by Scaleway |
| Images | `kind load docker-image` from your laptop | Pulled from the Container Registry |
| Load balancer | No real external IP, ports 18080 and 18443 on your laptop | A real load balancer with a public IP |
| PVC | Folder on your laptop | Block Storage volume |
| TLS | mkcert certificates | Let's Encrypt |
| Secrets | From `values-local.yaml` | From Scaleway Secret Manager through External Secrets |
| Installing | `helm upgrade --install` from your laptop | Flux pulls it from Git |

If something works in kind, it usually works in Kapsule too.
The remaining risks sit in the right-hand column.

## Kapsule in more detail

### Node pools and autoscaling

A **node pool** is a group of nodes of the same type.
Our blueprint has two.

- The **system pool** always runs, with the fixed components such as Traefik, Zitadel, Postgres and the apps.
- The **compute pool** uses heavy machines with a lot of memory (type POP2), and has a minimum of zero nodes.
  Kubernetes only starts a node there when a Dagster run needs the room, and removes it again afterwards.

Because an empty pool costs nothing, you can switch the environment off cheaply by setting both pools to zero.

### Load balancer and nip.io

As soon as Traefik gets its `LoadBalancer` Service, Scaleway creates a load balancer with a public IP address, for example `51.15.1.2`.
`nip.io` is a free service that makes any name containing an IP point to that IP.
So `dagster.51.15.1.2.nip.io` automatically goes to `51.15.1.2`.
This gives us web addresses without having to arrange our own domain.

## The blueprint: hub and spoke

The blueprint in `deploy/scaleway/` spreads the infrastructure over several Scaleway **Projects**.
A Project is a separate space inside your Scaleway account, with its own permissions and its own billing line.

- The **hub** holds what all environments share, such as the Container Registry.
- A **spoke** is one environment, such as `dev`.
  It holds the Kapsule cluster, the lakehouse bucket, Secret Manager and the IAM keys.

The picture comes from a wheel.
The hub sits in the middle and the spokes are the spokes.
Adding `stage` or `prod` later means adding another spoke.

### Container Registry

The registry stores our Docker images.
Locally you build images and use them straight away.
In the cloud the nodes have to download them, so you push them to the registry first.

### Secret Manager and IAM keys

**Secret Manager** is Scaleway's vault for passwords and keys.
**IAM** controls who may do what.
The blueprint creates separate IAM keys that can each do one thing, for example only read and write the bucket, or only pull images.

### The mesh, and why it is off

The blueprint can set up a private network with **Headscale**, a self-hosted variant of Tailscale.
With it, the nodes and the Kubernetes API are not on the internet, and you can only reach them over a VPN connection.
That costs about €20 to €25 per month in extra machines.

For this proof of concept we switch the mesh off.
The Kubernetes API then stays public, but an ACL only lets our own IP address in.
Users notice nothing, because the web apps go through the load balancer.
The mesh comes back on as soon as customer data arrives.

## GitOps with Flux

Locally you install the chart yourself with `helm upgrade --install`.
In the cloud **Flux** does it for you.
Flux runs in the cluster, checks our Git repository every few minutes, and makes sure the cluster matches what is in Git.
This is called **GitOps**.

So you change the cloud environment by pushing a commit.
Flux reads the `deploy/gitops/clusters/scaleway/dev` folder.
Among other things it contains a **HelmRelease**, an object that tells Flux "install this chart with these values".

## External Secrets

Secrets must not go into Git, but Flux only reads Git.
**External Secrets Operator** (ESO) solves this.

- A **ClusterSecretStore** tells ESO how to reach Scaleway Secret Manager.
- An **ExternalSecret** says "create a Kubernetes Secret with this name, filled with these values from Secret Manager".

Git then only holds a reference to a secret, and the value itself stays in Scaleway.

## Terraform and state

The infrastructure itself (cluster, bucket, registry, keys) is described in **Terraform** files.
Terraform compares that description with what exists at Scaleway, and creates or deletes what is needed.
`plan` shows what would change beforehand, and `apply` carries it out.
The blueprint uses Terraform 1.10.3.
The older stack in `deploy/opentofu/` uses OpenTofu, which works almost identically.

Terraform remembers what it has created in a **state** file.
Ours lives in a bucket in the hub.
The state also contains the generated passwords, so that bucket must stay private.

## kubectl cheat sheet

`kubectl` is the command you use to talk to a cluster.
`kubectl config use-context` selects the cluster, for example `kind-srdp` for local.

| Command | What it does | In Compose |
|:---|:---|:---|
| `kubectl get pods -n srdp` | Lists all pods and their status | `docker compose ps` |
| `kubectl logs -n srdp <pod>` | Shows the logs of a pod | `docker compose logs <service>` |
| `kubectl logs -n srdp <pod> --previous` | Shows the logs from before the last crash | No equivalent |
| `kubectl describe pod -n srdp <pod>` | Shows details and events, such as why a pod does not start | `docker inspect` |
| `kubectl get events -n srdp --sort-by=.lastTimestamp` | Shows recent events in the namespace | No equivalent |
| `kubectl port-forward -n srdp svc/<service> 8080:80` | Makes a Service temporarily reachable on your laptop | `ports:` |
| `kubectl exec -it -n srdp <pod> -- sh` | Opens a shell in a pod | `docker compose exec <service> sh` |
| `kubectl get nodes` | Lists the nodes in the cluster | No equivalent |

### Statuses you will see often

| Status | Meaning |
|:---|:---|
| `Running` | The pod is running. |
| `Pending` | The pod is waiting, usually for a node with enough memory or for a volume. |
| `ContainerCreating` | The image is being pulled or the volume attached. |
| `ImagePullBackOff` | The image cannot be pulled. Check the name, the tag and the registry key. |
| `CrashLoopBackOff` | The container starts and keeps crashing. Check the logs with `--previous`. |
| `Completed` | A one-off task (a Job) has finished. |

## Glossary

| Term | Meaning |
|:---|:---|
| ACL | A list of IP addresses that are allowed access. |
| Bucket | A folder in object storage. |
| Chart | A Helm package of templates. |
| Cluster | The group of nodes plus the control plane. |
| Control plane | The brain of the cluster, including the API server. |
| Deployment | The description of which pods should run. |
| ESO | External Secrets Operator, which puts secrets from Secret Manager into the cluster. |
| Flux | The GitOps tool that keeps the cluster in sync with Git. |
| HelmRelease | A Flux object that installs a chart. |
| Hub | The shared part of the blueprint. |
| Ingress | A rule that routes a web address to a Service. |
| kind | Kubernetes on your laptop, in Docker. |
| Kapsule | Scaleway's managed Kubernetes. |
| Namespace | A group inside the cluster. |
| Node | One machine in the cluster. |
| Node pool | A group of nodes of the same type. |
| Object storage | File storage through an S3-compatible API. |
| Pod | One or more containers that run together. |
| PVC | A request for persistent storage. |
| Release | An installed chart. |
| Service | A fixed name and address for a group of pods. |
| Spoke | One environment in the blueprint, such as `dev`. |
| State | The file in which Terraform tracks what it has created. |
| Values | The settings Helm uses to fill in a chart. |
