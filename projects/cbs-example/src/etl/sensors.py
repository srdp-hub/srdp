"""Run-status sensor registering the Dagster → OpenLineage bridge for this project."""

import os

from dagster import (
    DagsterRunStatus,
    DefaultSensorStatus,
    RunStatusSensorContext,
    run_status_sensor,
)

from srdp.lineage.openlineage_bridge import emit_openlineage_events

MARQUEZ_URL = os.environ.get("MARQUEZ_URL", "http://marquez:5000")


@run_status_sensor(run_status=DagsterRunStatus.SUCCESS, default_status=DefaultSensorStatus.RUNNING)
def openlineage_sensor(context: RunStatusSensorContext) -> None:
    """Emit an OpenLineage event for every asset materialized in a successful run."""
    emit_openlineage_events(
        dagster_run=context.dagster_run,
        instance=context.instance,
        repository_def=context.repository_def,
        marquez_url=MARQUEZ_URL,
    )
