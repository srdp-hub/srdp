"""dbt-defined assets — pure SQL models against the same DuckLake catalog the Polars pipeline writes to."""

from pathlib import Path
from typing import Any

from dagster import AssetExecutionContext, AssetKey
from dagster_dbt import DagsterDbtTranslator, DbtCliResource, DbtProject, dbt_assets

cbs_dbt_project = DbtProject(project_dir=Path(__file__).resolve().parents[3] / "dbt")
# Always parse, not prepare_if_dev(): the code server runs `dagster code-server start`,
# never `dagster dev`, so prepare_if_dev()'s DAGSTER_IS_DEV_CLI check would no-op here.
cbs_dbt_project.preparer.prepare(cbs_dbt_project)


class CbsDbtTranslator(DagsterDbtTranslator):
    """Maps the dbt source `raw_population` onto the Polars asset of the same name.

    Without this, the dbt source would get its own disconnected asset key and the
    Dagster/lineage graph would not show that the dbt models actually depend on the
    Polars pipeline's output.
    """

    def get_asset_key(self, dbt_resource_props: dict[str, Any]) -> AssetKey:
        """Map the dbt source `raw_population` onto the existing Polars asset key.

        Args:
            dbt_resource_props: The dbt node's resolved manifest properties.

        Returns:
            The Polars asset key for the `raw_population` source, otherwise
            dagster-dbt's own default mapping.
        """
        if dbt_resource_props["resource_type"] == "source" and dbt_resource_props["name"] == "raw_population":
            return AssetKey(["raw_population"])
        return super().get_asset_key(dbt_resource_props)


@dbt_assets(manifest=cbs_dbt_project.manifest_path, dagster_dbt_translator=CbsDbtTranslator())
def cbs_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
    """Run the cbs_example dbt project (models + tests) via `dbt build`."""
    yield from dbt.cli(["build"], context=context).stream()
