"""Column-level lineage for Polars assets, built on `Expr.meta`.

Single-hop only: each output column is traced back to the input columns the given
expression directly references (`Expr.meta.root_names()`). This does not resolve
transitive multi-step chains (an expression built from an already-derived column
only reports that intermediate column, not its own origin) or full pipeline-plan
lineage across joins. It is opt-in by necessity: a materialized `pl.DataFrame`
carries no record of the expressions that produced it, so only the asset author,
who still has those expressions in scope, can supply the mapping.

See docs/reviews/poc-build-plan.md ("srdp.lineage: what Polars provides,
precisely") for the full scoping rationale.
"""

import polars as pl
from dagster import AssetKey, MetadataValue, TableColumnDep, TableColumnLineage


def column_lineage_metadata(
    exprs: dict[str, pl.Expr],
    upstream_asset_key: AssetKey | str | list[str],
) -> dict[str, MetadataValue]:
    """Build Dagster column-lineage metadata from the expressions that produced an asset's output.

    Args:
        exprs: Mapping of output column name to the Polars expression that produced it.
        upstream_asset_key: The single upstream asset every output column was derived from.

    Returns:
        A one-entry metadata dict (key ``"dagster/column_lineage"``) to merge into an
        `Output`'s metadata — renders as the column lineage graph in the Dagster UI.
    """
    deps_by_column = {
        output_col: [
            TableColumnDep(asset_key=upstream_asset_key, column_name=root_name) for root_name in expr.meta.root_names()
        ]
        for output_col, expr in exprs.items()
    }
    return {"dagster/column_lineage": MetadataValue.column_lineage(TableColumnLineage(deps_by_column=deps_by_column))}
