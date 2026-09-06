"""Income enrichment — landed via the API, not fetched by Dagster itself.

A different ingestion path from `example.py`'s CBS-fetching assets:
`income_by_province` never reaches out to a source, its data arrives as run
config, supplied by whoever triggers the run. In this demo that's
`srdp.api`'s `/ingest/income` endpoint, itself called from the Marimo
notebook — notebook to API to Dagster run to enriched table, no cron
schedule involved. Source data: CBS StatLine table 70072ned, "Gemiddeld
besteedbaar inkomen" (average disposable household income), same table
`raw_population` reads, different topic, and on a slower update cadence, its
latest period is 2024, while the population topic is already at 2026.
"""

import polars as pl
from dagster import Config, MetadataValue, Output, asset


class IncomeRow(Config):
    """One province's average disposable household income."""

    province: str
    avg_disposable_income_k_eur: float


class IncomeByProvinceConfig(Config):
    """Run config for `income_by_province` — the payload the API received."""

    rows: list[IncomeRow]


@asset(io_manager_key="ducklake_io_manager", kinds={"polars"})
def income_by_province(config: IncomeByProvinceConfig) -> Output[pl.DataFrame]:
    """Average disposable household income per province, from run config.

    Args:
        config: The rows supplied by whoever triggered this run.
    """
    df = pl.DataFrame([row.model_dump() for row in config.rows])
    return Output(
        df,
        metadata={
            "num_rows": len(df),
            "source": MetadataValue.url("https://opendata.cbs.nl/#/CBS/nl/dataset/70072ned/table"),
        },
    )


@asset(io_manager_key="ducklake_io_manager", kinds={"polars"})
def population_enriched(
    population_by_province: pl.LazyFrame,
    income_by_province: pl.LazyFrame,
) -> Output[pl.DataFrame]:
    """Population and average disposable income, joined per province."""
    population = population_by_province.collect()
    income = income_by_province.collect()
    result = population.join(income, on="province", how="inner").sort("total_population", descending=True)
    return Output(
        result,
        metadata={
            "num_provinces": len(result),
            "preview": MetadataValue.md(f"```\n{result}\n```"),
        },
    )
