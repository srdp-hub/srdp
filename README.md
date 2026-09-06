# The Single Repo Data Platform (SRDP)

> _aka the serendipitous data platform_

Because your data stack shouldn't require a PhD in Kubernetes and a second mortgage.

Welcome, weary data traveler. You've stumbled upon something serendipitous, a quixotic quest to build a sane, powerful, and actually usable data platform from the best open-source components we can find. All from the comfort of a single Git repository.[^1]

What's the big idea? To stop gluing together 87 different services with YAML, duct tape, and desperate Stack Overflow searches at 3 AM. We're assembling a dream team of data tools that play nicely together, so you can spend less time wrangling infrastructure and more time doing... well, whatever it is you data people do. Probably making fancy charts.

[^1]: We take inspiration from [Instant OpenHIE](https://openhie.github.io/instant/) project who have done the same for open source health information exchange platforms.
---

## The Dream Team: Our All-Star Roster
We didn't just pick these tools out of a hat. Okay, maybe a little. But mostly, we chose them because they're fast, modern, and don't make us want to throw our laptops out the window. Think of them as the Avengers of the data world, if the Avengers were less about smashing aliens and more about smashing GROUP BY queries.

Here's the lineup of our chosen champions:

| Component |	Role in the SRDP |	Our Unsolicited Opinion |
|:---|:---|:---|
| Zitadel	|The Bouncer / Identity & Access Management	| Manages who gets to touch the precious data. Because "SELECT * FROM users;" should require more than just a password of password123. |
| Traefik	|The Traffic Cop / Cloud Native Proxy	| Directs all the incoming requests so services don't crash into each other. It's the only traffic jam you'll actually enjoy.|
| Polars	|The Speed Demon / Dataframe Library	| It's like pandas, but it actually uses all your CPU cores and doesn't take a coffee break on df.groupby(). Written in Rust, because of course it is.|
| DuckDB	|The Pocket Rocket / In-Process OLAP DB	| An incredibly fast analytical database that runs inside your application. It's the power of a warehouse in the body of a library. Quack-tastic!|
| Dagster	|The Conductor / Data Orchestrator	| The sensible, type-aware adult in the room of chaotic data pipelines. It knows what your data assets are and helps you not set the whole factory on fire.|
| dlt-hub	|The Plumber / Data Loading Library	| Gets your data from "over there" to "right here" with surprisingly little fuss. Turns messy APIs into clean tables faster than you can say "ETL is dead".|
| dbt	    | The Transformer / Data Transformation Tool	| The "T" in ELT that everyone's talking about. It turns your analysts into data engineering heroes with the power of SELECT statements and Jinja.|
| marimo	|The Mad Scientist's Notebook	| A next-gen reactive Python notebook. Change one cell, and the whole notebook updates. It's like magic, but with fewer rabbits and more legible code.|
| Quarto	|The Storyteller / Scientific Publishing	| Turns your brilliant analysis into beautiful reports, presentations, and websites. Because data that isn't shared is just sad, lonely numbers.|

---

## Deployment Targets

SRDP ships deployment scripts for two targets.

### Docker Compose

Located in `deploy/docker/`, this stack runs SRDP via Docker compose, but without Dagster. This was the first iteration of SRDP, focusing on the integration of Traefik and Zitadel.

| File | Purpose |
|:---|:---|
| `deploy/docker/docker-compose.yml` | Main Compose definition for all services |
| `deploy/docker/docker-compose.override.yml` | Local TLS: mounts self-signed mkcert certificates into Traefik |
| `deploy/docker/docker-compose.prod.yml` | Production override: switches Traefik to Let's Encrypt ACME for TLS |
| `deploy/docker/.env.example` | Template for required environment variables (secrets, OIDC client config) |
| `config/traefik/traefik.yml` | Traefik static configuration (entrypoints, TLS, ACME, dashboard) |

An OpenTofu script is provided in `deploy/opentofu/gcp/` to deploy this Docker
Compose stack to a **Google Cloud Compute Engine** VM. It provisions a Debian
`e2-medium` instance with a static IP and firewall rules for ports 80 and 443,
then bootstraps Docker and Docker Compose via a startup script that clones the
repository, injects secrets, and starts the production Compose stack with
Let's Encrypt TLS.

### Kubernetes + OpenTofu

Located in `deploy/kubernetes/`, this target deploys the full platform on a managed
Kubernetes cluster. Infrastructure is provisioned with [OpenTofu](https://opentofu.org/);
applications are deployed via a Helm umbrella chart.

| Directory / File | Purpose |
|:---|:---|
| `deploy/opentofu/scaleway/` | OpenTofu configuration to provision a **Scaleway Kapsule** managed Kubernetes cluster, VPC, autoscaling node pool, and container registry |
| `deploy/kubernetes/srdp-chart/` | Helm umbrella chart bundling Traefik, Zitadel, PostgreSQL, OAuth2-Proxy, and Dagster as upstream dependencies, plus custom templates for Marimo, Quarto, and TLS certificate bootstrapping |
| `projects/cbs-example/` | Dagster user code deployment (assets, jobs, schedules) built as a separate container image |

A `Justfile` at the repository root provides convenience commands for both targets:

```bash
just local-deploy    # Deploy to a local Kubernetes cluster (e.g. kind/k3s)
just prod-full       # Full production deploy on Scaleway
just prod-apply      # Provision Scaleway infrastructure with OpenTofu
```

---

## Documentation

Full documentation is available at **[srdp-hub.github.io/srdp](https://srdp-hub.github.io/srdp/)**.

**Preview locally:**
```bash
uvx zensical serve
```
Opens the site at `localhost:8000` with live reload.

**Deployment:**
Documentation is built with [Zensical](https://zensical.org/) and deployed automatically to GitHub Pages on every push to `main` via the [Build and deploy Documentation](.github/workflows/docs.yml) GitHub Actions workflow.

---

![](./docs/images/architecture.png)
