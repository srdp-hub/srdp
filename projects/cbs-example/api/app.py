"""cbs-example's own API add-on: income ingestion, mounted onto the platform's core API.

This project's provinces and its `enrichment_job` are specifics of this one
example, not platform behavior, so they live here rather than in
`srdp.api.main` (see AGENTS.md: tenant-specific content belongs in
`projects/<name>/`). `create_app` mounts this router alongside the platform's
own endpoints, so both share one auth edge and one set of OpenAPI docs.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from srdp.api.main import DAGSTER_REPOSITORY_LOCATION, DAGSTER_REPOSITORY_NAME, create_app, dagster_graphql

_KNOWN_PROVINCES = {
    "Groningen",
    "Fryslân",
    "Drenthe",
    "Overijssel",
    "Flevoland",
    "Gelderland",
    "Utrecht",
    "Noord-Holland",
    "Zuid-Holland",
    "Zeeland",
    "Noord-Brabant",
    "Limburg",
}


class IncomeRow(BaseModel):
    """One province's average disposable household income, in €1,000s."""

    province: str
    avg_disposable_income_k_eur: float


class IngestIncomeRequest(BaseModel):
    """Request body for `/ingest/income`."""

    rows: list[IncomeRow]


router = APIRouter()


@router.post("/ingest/income")
async def ingest_income(payload: IngestIncomeRequest) -> dict:
    """Land income data and launch the Dagster run that enriches the catalog with it.

    Demonstrates a push-based ingestion path: the caller (e.g. a notebook)
    supplies fresh data directly, rather than Dagster reaching out to fetch
    it. The data becomes the run config for the ``income_by_province``
    asset, which the ``enrichment_job`` materializes alongside
    ``population_enriched``.

    Args:
        payload: The income rows to land, one per province.

    Returns:
        A dict with the launched run's ``run_id``.

    Raises:
        HTTPException: 422 if ``rows`` is empty or names an unknown province,
            502 if Dagster fails to launch the run.
    """
    if not payload.rows:
        raise HTTPException(status_code=422, detail="rows must not be empty")
    unknown = {row.province for row in payload.rows} - _KNOWN_PROVINCES
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown provinces: {sorted(unknown)}")

    query = """
    mutation LaunchRun($selector: JobOrPipelineSelector!, $runConfigData: RunConfigData) {
      launchRun(executionParams: {selector: $selector, runConfigData: $runConfigData}) {
        __typename
        ... on LaunchRunSuccess { run { runId } }
        ... on PythonError { message }
        ... on RunConfigValidationInvalid { errors { message } }
        ... on PipelineNotFoundError { message }
      }
    }
    """
    variables = {
        "selector": {
            "jobName": "enrichment_job",
            "repositoryName": DAGSTER_REPOSITORY_NAME,
            "repositoryLocationName": DAGSTER_REPOSITORY_LOCATION,
        },
        "runConfigData": {
            "ops": {
                "income_by_province": {
                    "config": {"rows": [row.model_dump() for row in payload.rows]},
                },
            },
        },
    }
    data = await dagster_graphql(query, variables)
    result = data["launchRun"]
    if result["__typename"] != "LaunchRunSuccess":
        raise HTTPException(status_code=502, detail=result)
    return {"run_id": result["run"]["runId"], "status": "launched"}


app = create_app(extra_routers=[router])
