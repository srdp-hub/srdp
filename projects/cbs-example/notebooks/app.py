"""Interactive lakehouse explorer and API push demo, over the CBS population data."""

import marimo as mo

app = mo.App()


@app.cell
def __():
    import marimo as mo

    from srdp.io.ducklake import setup_ducklake

    conn = setup_ducklake()
    return conn, mo


@app.cell
def __(mo):
    mo.md(
        """
        # SRDP Lakehouse Explorer

        Live queries against the DuckLake catalog — the same catalog the Polars
        pipeline and its dbt models write to. Nothing here is precomputed; every
        cell below queries the lake directly.
        """
    )


@app.cell
def __(conn, mo):
    provinces = conn.sql("SELECT * FROM ducklake.main.population_by_province ORDER BY total_population DESC").pl()
    mo.md("## Population by province")
    return (provinces,)


@app.cell
def __(provinces):
    provinces


@app.cell
def __(mo, provinces):
    province_picker = mo.ui.dropdown(
        options=provinces["province"].to_list(),
        value=provinces["province"][0],
        label="Province",
    )
    province_picker
    return (province_picker,)


@app.cell
def __(conn, mo, province_picker):
    breakdown = conn.execute(
        """
        SELECT age_bracket, population
        FROM ducklake.main.raw_population
        WHERE province = ?
        ORDER BY age_bracket
        """,
        [province_picker.value],
    ).pl()
    mo.md(f"## Age breakdown — {province_picker.value}")
    return (breakdown,)


@app.cell
def __(breakdown):
    breakdown


@app.cell
def __(mo):
    mo.md(
        """
        ## Cross-check: Polars vs. dbt

        Two independent pipelines compute the same aggregation — one in Polars,
        one in dbt SQL. `match` confirms they agree.
        """
    )


@app.cell
def __(conn):
    cross_check = conn.sql(
        """
        SELECT
            p.province,
            p.total_population AS polars_total,
            d.total_population AS dbt_total,
            p.total_population = d.total_population AS match
        FROM ducklake.main.population_by_province p
        JOIN ducklake.main.population_by_province_dbt d USING (province)
        ORDER BY p.total_population DESC
        """
    ).pl()
    return (cross_check,)


@app.cell
def __(cross_check):
    cross_check


@app.cell
def __(mo):
    mo.md(
        """
        ## Push-based enrichment: notebook to API to Dagster

        Every asset above is fetched by Dagster itself, on its own schedule.
        This one runs the other direction: this notebook fetches fresh CBS
        income data live, POSTs it to `srdp.api`'s `/ingest/income` endpoint,
        which launches a Dagster run — `income_by_province` takes the payload
        as its run config instead of fetching anything, and `population_enriched`
        joins it against the existing population data. No cron schedule
        involved, the run only happens because this cell called the API.
        """
    )


@app.cell
def __():
    import json
    import time
    import urllib.parse
    import urllib.request

    CBS_TABLE = "70072ned"
    CBS_BASE = f"https://opendata.cbs.nl/ODataApi/odata/{CBS_TABLE}"
    INCOME_TOPIC = "ParticuliereHuishoudensExclStudenten_96"  # "Gemiddeld besteedbaar inkomen"
    API_BASE = "http://api:8000"  # internal Docker network, same as Traefik would route to

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
    return API_BASE, CBS_BASE, INCOME_TOPIC, PROVINCES, json, time, urllib


@app.cell
def __(CBS_BASE, INCOME_TOPIC, PROVINCES, json, mo, urllib):
    # Income stats lag population stats — 2024 is the latest period with data
    # on this topic, unlike raw_population's 2026 (see enrichment.py's docstring).
    province_filter = " or ".join(f"RegioS eq '{code}  '" for code in PROVINCES)
    income_query = urllib.parse.urlencode(
        {
            "$format": "json",
            "$filter": f"Perioden eq '2024JJ00' and ({province_filter})",
            "$select": f"RegioS,{INCOME_TOPIC}",
        }
    )
    with urllib.request.urlopen(f"{CBS_BASE}/TypedDataSet?{income_query}") as income_resp:
        income_rows = json.load(income_resp)["value"]

    income_payload = {
        "rows": [
            {
                "province": PROVINCES[row["RegioS"].strip()],
                "avg_disposable_income_k_eur": row[INCOME_TOPIC],
            }
            for row in income_rows
        ]
    }
    mo.md(f"Fetched income data for {len(income_payload['rows'])} provinces from CBS.")
    return (income_payload,)


@app.cell
def __(income_payload, mo):
    income_payload
    push_button = mo.ui.run_button(label="Push to API and trigger enrichment")
    push_button
    return (push_button,)


@app.cell
def __(API_BASE, income_payload, json, mo, push_button, urllib):
    mo.stop(not push_button.value, mo.md("Click the button above to push the data."))

    push_request = urllib.request.Request(
        f"{API_BASE}/ingest/income",
        data=json.dumps(income_payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(push_request) as push_resp:
        launch_result = json.load(push_resp)
    run_id = launch_result["run_id"]
    mo.md(f"Run launched: `{run_id}`")
    return (run_id,)


@app.cell
def __(API_BASE, json, mo, run_id, time, urllib):
    # Poll until the run finishes, or give up after 60s.
    status = "STARTED"
    for _ in range(30):
        runs_query = urllib.parse.urlencode({"limit": "1"})
        with urllib.request.urlopen(f"{API_BASE}/dagster/runs?{runs_query}") as poll_resp:
            runs = json.load(poll_resp)["runs"]
        matching = [r for r in runs if r["runId"] == run_id]
        if matching and matching[0]["status"] in ("SUCCESS", "FAILURE"):
            status = matching[0]["status"]
            break
        time.sleep(2)
    mo.md(f"Run `{run_id}` status: **{status}**")
    return (status,)


@app.cell
def __(mo):
    mo.md("## Enriched result — population and income, joined")


@app.cell
def __(conn, mo, status):
    mo.stop(status != "SUCCESS", mo.md("Waiting on a successful run before querying the enriched table."))
    enriched = conn.sql("SELECT * FROM ducklake.main.population_enriched ORDER BY total_population DESC").pl()
    return (enriched,)


@app.cell
def __(enriched):
    enriched


if __name__ == "__main__":
    app.run()
