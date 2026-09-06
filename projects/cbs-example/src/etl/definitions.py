"""The cbs-example code location: jobs, schedules, resources, and the asset graph."""

from dagster import Definitions, ScheduleDefinition, define_asset_job
from dagster_dbt import DbtCliResource

from etl.assets.dbt_assets import cbs_dbt_assets, cbs_dbt_project
from etl.assets.enrichment import income_by_province, population_enriched
from etl.assets.example import (
    executive_summary,
    population_by_age_group,
    population_by_province,
    raw_population,
    top_province,
)
from etl.sensors import openlineage_sensor
from srdp.io.ducklake import ducklake_io_manager
from srdp.resources.k8s import (
    BACKFILL_K8S_CONFIG,
    BASE_RUN_K8S_CONFIG,
    FAST_LANE_K8S_CONFIG,
)

# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

srdp_etl_job = define_asset_job(
    "srdp_etl_job",
    selection=[
        raw_population,
        population_by_province,
        population_by_age_group,
        top_province,
        executive_summary,
        cbs_dbt_assets,
    ],
    tags={
        "dagster-k8s/config": BASE_RUN_K8S_CONFIG,
        "dagster/priority": "0",
        "team": "data-platform",
        "workload_kind": "scheduled-etl",
    },
)

# Not scheduled, deliberately: `income_by_province` takes its data as run
# config rather than fetching it, so it has nothing to run against unless
# something (srdp.api's /ingest/income endpoint) supplies it. Keeping it out
# of srdp_etl_job's selection means the cron schedule never tries to
# materialize it with an empty payload and overwrite real landed data.
enrichment_job = define_asset_job(
    "enrichment_job",
    selection=[income_by_province, population_enriched],
    tags={
        "dagster-k8s/config": BASE_RUN_K8S_CONFIG,
        "dagster/priority": "0",
        "team": "data-platform",
        "workload_kind": "triggered-enrichment",
    },
)

executive_summary_job = define_asset_job(
    "executive_summary_job",
    selection=["executive_summary", "top_province"],
    tags={
        "dagster-k8s/config": FAST_LANE_K8S_CONFIG,
        "dagster/priority": "5",
        "team": "data-platform",
        "workload_kind": "fast-lane",
    },
)

srdp_etl_backfill_job = define_asset_job(
    "srdp_etl_backfill_job",
    selection=[
        raw_population,
        population_by_province,
        population_by_age_group,
        top_province,
        executive_summary,
        cbs_dbt_assets,
    ],
    tags={
        "dagster-k8s/config": BACKFILL_K8S_CONFIG,
        "dagster/priority": "-2",
        "team": "data-platform",
        "workload_kind": "backfill",
    },
)

# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

etl_schedule = ScheduleDefinition(
    job=srdp_etl_job,
    cron_schedule="*/5 * * * *",
    tags={"dagster/priority": "-1"},
)

# ---------------------------------------------------------------------------
# Top-level export — Dagster gRPC server loads this via `-m etl.definitions`
# ---------------------------------------------------------------------------

defs = Definitions(
    assets=[
        raw_population,
        population_by_province,
        population_by_age_group,
        top_province,
        executive_summary,
        cbs_dbt_assets,
        income_by_province,
        population_enriched,
    ],
    jobs=[srdp_etl_job, executive_summary_job, srdp_etl_backfill_job, enrichment_job],
    schedules=[etl_schedule],
    sensors=[openlineage_sensor],
    resources={
        "ducklake_io_manager": ducklake_io_manager,
        "dbt": DbtCliResource(project_dir=cbs_dbt_project),
    },
)
