"""Regional population demo pipeline — Dutch open data from CBS.

Source: CBS StatLine table 70072ned ("Regionale kerncijfers Nederland"), fetched
live over the CBS OData v3 API (https://opendata.cbs.nl). Public, openly licensed
government data — replaces the earlier synthetic e-commerce example.
"""

import json
import urllib.parse
import urllib.request

import polars as pl
from dagster import MetadataValue, Output, asset

from srdp.lineage import column_lineage_metadata

CBS_TABLE = "70072ned"
CBS_BASE = f"https://opendata.cbs.nl/ODataApi/odata/{CBS_TABLE}"

# CBS RegioS codes for the twelve Dutch provinces (stable dimension values on this table).
PROVINCES = {
    "PV20": "Groningen",
    "PV21": "Fryslân",
    "PV22": "Drenthe",
    "PV23": "Overijssel",
    "PV24": "Flevoland",
    "PV25": "Gelderland",
    "PV26": "Utrecht",
    "PV27": "Noord-Holland",
    "PV28": "Zuid-Holland",
    "PV29": "Zeeland",
    "PV30": "Noord-Brabant",
    "PV31": "Limburg",
}

# CBS age-bracket topic keys on this table, mapped to a readable label.
AGE_BRACKETS = {
    "k_0Tot15Jaar_4": "0-15",
    "k_15Tot25Jaar_5": "15-25",
    "k_25Tot45Jaar_6": "25-45",
    "k_45Tot65Jaar_7": "45-65",
    "k_65Tot80Jaar_8": "65-80",
    "k_80JaarOfOuder_9": "80+",
}


def _cbs_get(path: str, **params: str) -> list[dict]:
    """Fetch and JSON-decode one CBS OData v3 endpoint."""
    query = urllib.parse.urlencode({"$format": "json", **params})
    with urllib.request.urlopen(f"{CBS_BASE}/{path}?{query}") as resp:  # noqa: S310
        return json.load(resp)["value"]


def _latest_period() -> str:
    """Return the most recent annual period key on this table (e.g. '2026JJ00')."""
    periods = _cbs_get("Perioden")
    annual = [p["Key"] for p in periods if p["Key"].endswith("JJ00")]
    return annual[-1]


@asset(io_manager_key="ducklake_io_manager", kinds={"polars"})
def raw_population() -> Output[pl.DataFrame]:
    """Population per Dutch province and age bracket, fetched live from CBS StatLine."""
    period = _latest_period()
    province_filter = " or ".join(f"RegioS eq '{code}  '" for code in PROVINCES)
    rows = _cbs_get(
        "TypedDataSet",
        **{
            "$filter": f"Perioden eq '{period}' and ({province_filter})",
            "$select": "RegioS,Perioden," + ",".join(AGE_BRACKETS),
        },
    )
    df = (
        pl.DataFrame(rows)
        .with_columns(pl.col("RegioS").str.strip_chars())
        .unpivot(
            on=list(AGE_BRACKETS),
            index=["RegioS", "Perioden"],
            variable_name="age_bracket_key",
            value_name="population",
        )
        .with_columns(
            pl.col("RegioS").replace(PROVINCES).alias("province"),
            pl.col("age_bracket_key").replace(AGE_BRACKETS).alias("age_bracket"),
            pl.col("population").cast(pl.Int64),
        )
        .select("province", "age_bracket", "population", "Perioden")
        .rename({"Perioden": "period"})
    )
    return Output(
        df,
        metadata={
            "num_rows": len(df),
            "period": period,
            "provinces": MetadataValue.text(", ".join(sorted(PROVINCES.values()))),
            "source": MetadataValue.url("https://opendata.cbs.nl/#/CBS/nl/dataset/70072ned/table"),
        },
    )


@asset(io_manager_key="ducklake_io_manager", kinds={"polars"})
def population_by_province(raw_population: pl.LazyFrame) -> Output[pl.DataFrame]:
    """Total population per province, summed across age brackets."""
    raw_population = raw_population.collect()
    province_expr = pl.col("province")
    total_expr = pl.col("population").sum().alias("total_population")
    result = raw_population.group_by(province_expr).agg(total_expr).sort("total_population", descending=True)
    return Output(
        result,
        metadata={
            "num_provinces": len(result),
            "top_province": result.row(0, named=True)["province"],
            "preview": MetadataValue.md(f"```\n{result}\n```"),
            **column_lineage_metadata(
                {"province": province_expr, "total_population": total_expr},
                upstream_asset_key="raw_population",
            ),
        },
    )


@asset(io_manager_key="ducklake_io_manager", kinds={"polars"})
def population_by_age_group(raw_population: pl.LazyFrame) -> Output[pl.DataFrame]:
    """Total population per age bracket, summed across provinces."""
    raw_population = raw_population.collect()
    age_bracket_expr = pl.col("age_bracket")
    total_expr = pl.col("population").sum().alias("total_population")
    result = raw_population.group_by(age_bracket_expr).agg(total_expr).sort("total_population", descending=True)
    return Output(
        result,
        metadata={
            "num_age_groups": len(result),
            "largest_age_group": result.row(0, named=True)["age_bracket"],
            "preview": MetadataValue.md(f"```\n{result}\n```"),
            **column_lineage_metadata(
                {"age_bracket": age_bracket_expr, "total_population": total_expr},
                upstream_asset_key="raw_population",
            ),
        },
    )


@asset
def top_province(population_by_province: pl.LazyFrame) -> Output[str]:
    """Pick the single most populous province."""
    winner = population_by_province.collect().row(0, named=True)
    return Output(
        winner["province"],
        metadata={
            "province": winner["province"],
            "population": winner["total_population"],
        },
    )


@asset
def executive_summary(
    population_by_province: pl.LazyFrame,
    population_by_age_group: pl.LazyFrame,
) -> Output[str]:
    """Produce a short text summary of the Dutch population breakdown."""
    population_by_province = population_by_province.collect()
    population_by_age_group = population_by_age_group.collect()
    top_p = population_by_province.row(0, named=True)
    top_a = population_by_age_group.row(0, named=True)
    total = population_by_province["total_population"].sum()
    summary = (
        f"Total population (12 provinces): {total:,}\n"
        f"Largest province: {top_p['province']} ({top_p['total_population']:,})\n"
        f"Largest age group: {top_a['age_bracket']} ({top_a['total_population']:,})\n"
        f"Provinces covered: {len(population_by_province)}\n"
        f"Age brackets: {len(population_by_age_group)}"
    )
    return Output(
        summary,
        metadata={"summary": MetadataValue.md(f"```\n{summary}\n```")},
    )
