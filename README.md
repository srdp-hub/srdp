# The Single-Repo Data Platform (SRDP)

SRDP is a self-hostable data platform assembled from established open-source components (Zitadel, Traefik, Dagster, dbt, DuckDB/DuckLake, Polars, marimo, and more) and deployed from a single Git repository. It is aimed at teams that want a coherent, governable data stack without operating a large set of separately managed services.[^1]

The same logical architecture runs from a laptop (Docker Compose) to a single VM to a Kubernetes cluster, so development and production stay aligned.

[^1]: The single-repository approach takes inspiration from the [Instant OpenHIE](https://openhie.github.io/instant/) project, which packages an open-source health information exchange into a single deployment recipe.

---

## Documentation

Full documentation, including the component list, architecture, and both deployment targets (Docker Compose and Kubernetes + OpenTofu), is available at **[docs.srdphub.com](https://docs.srdphub.com)**.

**Preview locally:**
```bash
uvx zensical serve
```
Opens the site at `localhost:8000` with live reload.

**Deployment:**
Documentation is built with [Zensical](https://zensical.org/) and deployed automatically to GitHub Pages on every push to `main` via the [Build and deploy Documentation](.github/workflows/docs.yml) GitHub Actions workflow.

---

## License

SRDP is licensed under the [Mozilla Public License 2.0](LICENSE).
