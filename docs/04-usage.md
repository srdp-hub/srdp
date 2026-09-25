---
title: 4. Usage & Verification
icon: lucide/circle-play
---

# Usage & Verification

### Check the release
- `helm list -n srdp`
- `kubectl get pods,svc,ing -n srdp`

### Access services
- Marimo: `https://marimo.srdp.localhost`
- Dagster: `https://dagster.srdp.localhost`
- Zitadel: `https://auth.srdp.localhost`
- Traefik dashboard (if enabled in values): `http://localhost:8080`

All apps (Marimo, Dagster) are protected behind OAuth2-Proxy. Accessing any of them will redirect to Zitadel for OIDC login before granting access. Quarto is disabled by default, see `docs/02-configuration.md`.

### Update or remove the release
- Re-apply updated values: rerun the `helm upgrade --install ...` command from [02-configuration.md](./02-configuration.md) (or `just local-deploy`).
- Remove everything: `helm uninstall srdp -n srdp`
  - If you also want to clear persistent data: `kubectl delete pvc --all -n srdp`
- Or use the task runner: `just local-delete`

### Logs
- Watch all pods: `kubectl logs -n srdp -l app.kubernetes.io/instance=srdp -f`
- Specific service, e.g. Marimo: `kubectl logs -n srdp deploy/marimo -f`
- Dagster webserver: `kubectl logs -n srdp deploy/srdp-dagster-webserver -f`
- Dagster daemon: `kubectl logs -n srdp deploy/srdp-dagster-daemon -f`
Once you have completed the setup, you can verify that all services are running correctly by accessing them in your web browser.

### Accessing Services

Use the following URLs. You will be prompted to authenticate before accessing each service. You can use the admin credentials from [02-configuration.md](./02-configuration.md):

*   **Marimo Dashboard:**
    *   URL: [https://marimo.srdp.localhost](https://marimo.srdp.localhost)
    *   You should see an interactive dashboard with a slider.

*   **Traefik Dashboard (for debugging):**
    *   URL: [http://localhost:8080](http://localhost:8080)
    *   This provides a view of Traefik's configuration, including all detected routers and services. It is very useful for troubleshooting routing issues.

### Managing the Services

You can manage the containers using standard `docker-compose` commands:

*   **To build and start the services:**
    ```bash
    docker-compose up --build # Remove --build if no changes were made
    ```

*   **To stop the services:**
    ```bash
    docker-compose down
    ```

    **Do not use `docker-compose down -v` unless you want to destroy all persistent data, including your Zitadel configuration.**

*   **To view the logs of all running services:**
    ```bash
    docker-compose logs -f
    ```

*   **To view the logs of a specific service (e.g., marimo):**
    ```bash
    docker-compose logs -f marimo
    ```
