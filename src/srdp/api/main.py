"""Read-only FastAPI surface: DuckLake catalog info and basic Dagster status.

Demo-scoped implementation of ADR-0002's "Level 1" unified API surface: enough
to browse what's in the catalog and what Dagster has been doing, without the
capability-token/JWT validation machinery ADR-0002/0008 describe for a
production deployment. Sits behind the same Traefik/oauth2-proxy edge gate as
every other service, same as Marimo/Streamlit (ADR-0007 Tier A read path).

Only the platform's own, tenant-agnostic endpoints live here. A project adds
its own endpoints (e.g. `projects/cbs-example/api/app.py`) by building an
`APIRouter` and passing it to `create_app`, rather than adding routes to this
module, so `srdp.api` stays free of any one tenant's specifics.
"""

import logging
import os
from collections.abc import Iterable
from html import escape

import duckdb
import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from srdp.io.ducklake import setup_ducklake

logger = logging.getLogger("srdp.api")

DAGSTER_GRAPHQL_URL = os.environ.get("DAGSTER_GRAPHQL_URL", "http://dagster-webserver:3000/graphql")
DAGSTER_REPOSITORY_LOCATION = os.environ.get("DAGSTER_REPOSITORY_LOCATION", "grpc:srdp-dagster-code:3030")
DAGSTER_REPOSITORY_NAME = os.environ.get("DAGSTER_REPOSITORY_NAME", "__repository__")

router = APIRouter()

_conn: duckdb.DuckDBPyConnection | None = None


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return the shared DuckLake connection, creating it on first use.

    Returns:
        A DuckDB connection with the ``ducklake`` catalog attached.
    """
    global _conn  # noqa: PLW0603
    if _conn is None:
        _conn = setup_ducklake()
    return _conn


def _table_exists(conn: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_catalog = 'ducklake' AND table_schema = ? AND table_name = ?",
        [schema, table],
    ).fetchone()
    return row is not None


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def dagster_graphql(query: str, variables: dict | None = None) -> dict:
    """Run a GraphQL query/mutation against Dagster's webserver.

    Exposed (not module-private) so a tenant's own `APIRouter` can launch
    runs or read Dagster state without duplicating the HTTP client setup.

    Args:
        query: The GraphQL document.
        variables: Query variables, if any.

    Returns:
        The response's `data` payload.

    Raises:
        HTTPException: 502 if Dagster returns GraphQL errors.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(DAGSTER_GRAPHQL_URL, json={"query": query, "variables": variables or {}})
        response.raise_for_status()
        payload = response.json()
    if "errors" in payload:
        raise HTTPException(status_code=502, detail=payload["errors"])
    return payload["data"]


@router.get("/health")
def health() -> dict:
    """Liveness check, does not touch the catalog or Dagster.

    Returns:
        A static status payload.
    """
    return {"status": "ok"}


@router.get("/token", response_class=HTMLResponse)
def token_page(request: Request) -> str:
    """Show the signed-in user their own access token, for calling the API outside a browser.

    Reads the identity/token headers Traefik's forward-auth already
    established for this request (oauth2-proxy's `--set-xauthrequest`
    output, listed on the `zitadel-auth` middleware's
    `authResponseHeaders`). oauth2-proxy also accepts this same token back
    as a `Bearer` header on future requests (`--skip-jwt-bearer-tokens` +
    `--extra-jwt-issuers`), so no separate API-key system exists, this is
    the user's own Zitadel access token.

    Args:
        request: The incoming request, read for the forwarded headers.

    Returns:
        An HTML page with the token and example `curl` usage.
    """
    email = request.headers.get("x-auth-request-email") or request.headers.get("x-auth-request-user") or "unknown"
    token = request.headers.get("x-auth-request-access-token")

    if not token:
        body = "<p>No access token on this request. Reload after signing in via the hub.</p>"
    else:
        example = f'curl -H "Authorization: Bearer {token}" \\\n  https://api.srdp.localhost/catalog/tables'
        body = f"""
        <p>Signed in as <strong>{escape(email)}</strong>.</p>
        <p>This is your own Zitadel access token, short-lived (Zitadel's default token lifetime).
        Reload this page for a fresh one once it expires.</p>
        <pre><code>{escape(token)}</code></pre>
        <p>Use it as a <code>Bearer</code> token to call the API from outside the browser:</p>
        <pre><code>{escape(example)}</code></pre>
        """

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>SRDP API access</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 46rem; margin: 3rem auto; padding: 0 1rem; }}
pre {{ background: #1a1a1a; color: #eee; padding: 1rem; border-radius: 6px; overflow-x: auto; }}
code {{ font-family: "JetBrains Mono", monospace; }}
</style></head>
<body>
<h1>SRDP API</h1>
{body}
<p><a href="/docs">Interactive API docs (Swagger UI)</a></p>
</body></html>"""


@router.get("/catalog/schemas")
def list_schemas() -> dict:
    """List every schema in the DuckLake catalog.

    Returns:
        A dict with the schema name list under ``schemas``.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT schema_name FROM information_schema.schemata WHERE catalog_name = 'ducklake' ORDER BY schema_name",
    ).fetchall()
    return {"schemas": [r[0] for r in rows]}


@router.get("/catalog/tables")
def list_tables(schema: str | None = None) -> dict:
    """List tables in the DuckLake catalog, optionally filtered to one schema.

    Args:
        schema: Restrict results to this schema, if given.

    Returns:
        A dict with the table list under ``tables``, each entry a
        ``{schema, table}`` pair.
    """
    conn = get_connection()
    if schema is not None:
        rows = conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_catalog = 'ducklake' AND table_schema = ? ORDER BY table_schema, table_name",
            [schema],
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_catalog = 'ducklake' ORDER BY table_schema, table_name",
        ).fetchall()
    return {"tables": [{"schema": r[0], "table": r[1]} for r in rows]}


@router.get("/catalog/tables/{schema}/{table}")
def table_info(schema: str, table: str) -> dict:
    """Return column schema and row count for one DuckLake table.

    Args:
        schema: Schema the table lives in.
        table: Table name.

    Returns:
        A dict with ``schema``, ``table``, ``row_count``, and ``columns``
        (each a ``{name, type}`` pair).

    Raises:
        HTTPException: 404 if the table does not exist in the catalog.
    """
    conn = get_connection()
    if not _table_exists(conn, schema, table):
        raise HTTPException(status_code=404, detail=f"Table {schema}.{table} not found")

    ref = f"ducklake.{_quote_identifier(schema)}.{_quote_identifier(table)}"
    described = conn.sql(f"DESCRIBE {ref}").fetchall()
    row_count = conn.sql(f"SELECT COUNT(*) FROM {ref}").fetchone()  # noqa: S608

    return {
        "schema": schema,
        "table": table,
        "row_count": row_count[0] if row_count else 0,
        "columns": [{"name": col_name, "type": col_type} for col_name, col_type, *_ in described],
    }


@router.get("/catalog/tables/{schema}/{table}/preview")
def table_preview(schema: str, table: str, limit: int = 10) -> dict:
    """Return a row preview for one DuckLake table.

    Args:
        schema: Schema the table lives in.
        table: Table name.
        limit: Maximum rows to return, capped at 100.

    Returns:
        A dict with ``rows``, each a JSON-friendly dict keyed by column name.

    Raises:
        HTTPException: 404 if the table does not exist in the catalog.
    """
    conn = get_connection()
    if not _table_exists(conn, schema, table):
        raise HTTPException(status_code=404, detail=f"Table {schema}.{table} not found")

    ref = f"ducklake.{_quote_identifier(schema)}.{_quote_identifier(table)}"
    capped_limit = min(limit, 100)
    result = conn.sql(f"SELECT * FROM {ref} LIMIT {capped_limit}")  # noqa: S608
    columns = [d[0] for d in result.description]
    rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
    return {"schema": schema, "table": table, "rows": rows}


@router.get("/dagster/runs")
async def dagster_runs(limit: int = 10) -> dict:
    """Return the most recent Dagster runs.

    Args:
        limit: Maximum runs to return, capped at 50.

    Returns:
        A dict with the run list under ``runs``.
    """
    query = """
    query RecentRuns($limit: Int!) {
      runsOrError(limit: $limit) {
        ... on Runs {
          results {
            runId
            status
            pipelineName
            startTime
            endTime
          }
        }
      }
    }
    """
    data = await dagster_graphql(query, {"limit": min(limit, 50)})
    return {"runs": data["runsOrError"].get("results", [])}


@router.get("/dagster/assets")
async def dagster_assets() -> dict:
    """Return the full Dagster asset key list.

    Returns:
        A dict with the asset key list under ``assets``, each entry a
        list of key path segments.
    """
    query = """
    query Assets {
      assetsOrError {
        ... on AssetConnection {
          nodes {
            key { path }
          }
        }
      }
    }
    """
    data = await dagster_graphql(query)
    nodes = data["assetsOrError"].get("nodes", [])
    return {"assets": [n["key"]["path"] for n in nodes]}


def create_app(extra_routers: Iterable[APIRouter] = ()) -> FastAPI:
    """Build the SRDP API: the platform's own endpoints plus any tenant add-ons.

    Args:
        extra_routers: Tenant-owned routers to mount alongside the core
            platform endpoints, each behind the same auth edge and appearing
            in the same OpenAPI docs.

    Returns:
        A configured FastAPI app.
    """
    app = FastAPI(
        title="SRDP API",
        description="Basic read-only info on the DuckLake catalog and Dagster.",
    )
    app.include_router(router)
    for extra_router in extra_routers:
        app.include_router(extra_router)
    return app


app = create_app()
