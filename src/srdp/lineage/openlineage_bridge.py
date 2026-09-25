"""Dagster → OpenLineage bridge.

Per ADR-0003, lineage is captured at the Dagster asset boundary, not inside each
library: this module never talks to DuckLake or Polars directly. It reads whatever
a materialization event already carries — dataset-level dependencies from the asset
graph, plus whatever metadata `DuckLakeIOManager` (schema, row count, null counts)
and `srdp.lineage.column_lineage_metadata` (opt-in column lineage) already attached
to it — and translates that into OpenLineage `RunEvent`s for a backend like Marquez.

Register `emit_openlineage_events` as a `@run_status_sensor(run_status=DagsterRunStatus.SUCCESS)`
in a project's `definitions.py`; this module does not register sensors itself, since
that has to happen per code location.
"""

import logging
import uuid
from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from datetime import UTC, datetime

from dagster import (
    DagsterInstance,
    DagsterRun,
    IntMetadataValue,
    MetadataValue,
    RepositoryDefinition,
    TableColumnLineageMetadataValue,
    TableSchemaMetadataValue,
)
from dagster._core.events import DagsterEventType
from openlineage.client import OpenLineageClient
from openlineage.client.event_v2 import (
    InputDataset,
    Job,
    OutputDataset,
    Run,
    RunEvent,
    RunState,
)
from openlineage.client.facet_v2 import (
    column_lineage_dataset,
    job_type_job,
    output_statistics_output_dataset,
    schema_dataset,
)

from srdp.io.ducklake import asset_key_path_to_table_ref

logger = logging.getLogger("srdp.lineage.openlineage_bridge")

_NAMESPACE = "ducklake"
_JOB_NAMESPACE = "srdp"


def _job_facets(kinds: AbstractSet[str]) -> dict:
    """Build the JobType facet from an asset's Dagster kind tags.

    This is the "filter by Polars vs. dbt" mechanism: `kinds` comes straight off
    the asset graph (`kinds={"polars"}` set explicitly on our assets;
    `kinds={"dbt", "duckdb"}` set automatically by dagster-dbt), so Marquez ends up
    with the same categorization Dagster's own UI already shows.
    """
    if "dbt" in kinds:
        integration = "dbt"
        job_type = "MODEL"
    elif "polars" in kinds:
        integration = "dagster"
        job_type = "polars"
    else:
        integration = "dagster"
        job_type = "ASSET"
    return {
        "jobType": job_type_job.JobTypeJobFacet(
            processingType="BATCH",
            integration=integration,
            jobType=job_type,
        ),
    }


def _dataset_facets(metadata: Mapping[str, MetadataValue]) -> dict:
    """Translate `DuckLakeIOManager`'s automatic metadata into OpenLineage dataset facets."""
    facets = {}
    schema = metadata.get("dagster/column_schema")
    if isinstance(schema, TableSchemaMetadataValue):
        facets["schema"] = schema_dataset.SchemaDatasetFacet(
            fields=[
                schema_dataset.SchemaDatasetFacetFields(name=col.name, type=col.type) for col in schema.value.columns
            ],
        )
    lineage = metadata.get("dagster/column_lineage")
    if isinstance(lineage, TableColumnLineageMetadataValue):
        fields = {
            output_col: column_lineage_dataset.Fields(
                inputFields=[
                    column_lineage_dataset.InputField(
                        namespace=_NAMESPACE,
                        name=dep.asset_key.to_user_string(),
                        field=dep.column_name,
                    )
                    for dep in deps
                ],
            )
            for output_col, deps in lineage.value.deps_by_column.items()
        }
        if fields:
            facets["columnLineage"] = column_lineage_dataset.ColumnLineageDatasetFacet(fields=fields)
    return facets


def _output_facets(metadata: Mapping[str, MetadataValue]) -> dict:
    row_count = metadata.get("dagster/row_count")
    if not isinstance(row_count, IntMetadataValue):
        return {}
    return {
        "outputStatistics": output_statistics_output_dataset.OutputStatisticsOutputDatasetFacet(
            rowCount=row_count.value,
        ),
    }


def emit_openlineage_events(
    dagster_run: DagsterRun,
    instance: DagsterInstance,
    repository_def: RepositoryDefinition,
    marquez_url: str,
) -> None:
    """Translate every asset materialized in a successful run into an OpenLineage RunEvent.

    Args:
        dagster_run: The completed run.
        instance: The Dagster instance to read materialization events from.
        repository_def: Used to read each asset's upstream dependencies and kind tags.
        marquez_url: Base URL of the Marquez (or any OpenLineage-compatible) backend.
    """
    client = OpenLineageClient(url=marquez_url)
    asset_graph = repository_def.asset_graph
    event_records = instance.get_records_for_run(
        run_id=dagster_run.run_id,
        of_type=DagsterEventType.ASSET_MATERIALIZATION,
    ).records

    for record in event_records:
        asset_key = record.asset_key
        asset_materialization = record.asset_materialization
        if asset_key is None or asset_materialization is None:
            logger.warning("Skipping materialization record with no asset key or materialization event.")
            continue
        metadata = asset_materialization.metadata
        node = asset_graph.get(asset_key)

        job = Job(namespace=_JOB_NAMESPACE, name=asset_key.to_user_string(), facets=_job_facets(node.kinds))
        run = Run(runId=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{dagster_run.run_id}:{asset_key.to_user_string()}")))

        has_dataset = "dagster/row_count" in metadata or "dagster/column_schema" in metadata
        outputs = None
        if has_dataset:
            table_ref = asset_key_path_to_table_ref(list(asset_key.path))
            outputs = [
                OutputDataset(
                    namespace=_NAMESPACE,
                    name=table_ref,
                    facets=_dataset_facets(metadata),
                    outputFacets=_output_facets(metadata),
                ),
            ]

        inputs = [
            InputDataset(namespace=_NAMESPACE, name=asset_key_path_to_table_ref(list(parent.key.path)))
            for parent in asset_graph.get_parents(node)
        ] or None

        event = RunEvent(
            eventTime=datetime.fromtimestamp(record.timestamp, tz=UTC).isoformat(),
            producer="https://github.com/srdp-hub/srdp",
            run=run,
            job=job,
            eventType=RunState.COMPLETE,
            inputs=inputs,
            outputs=outputs,
        )
        client.emit(event)
        logger.info("Emitted OpenLineage event for %s (run %s).", asset_key.to_user_string(), dagster_run.run_id)
