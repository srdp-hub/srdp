"""Population dashboard — a third way to build on top of SRDP.

Same DuckLake catalog as the Marimo notebook and dbt models, different tool:
a Streamlit dashboard, to show that any of these can sit on the same data.
"""

import duckdb
import polars as pl
import streamlit as st

from srdp.io.ducklake import setup_ducklake

st.set_page_config(page_title="SRDP Population Dashboard", page_icon=":material/bar_chart:", layout="wide")


@st.cache_resource
def get_connection() -> duckdb.DuckDBPyConnection:
    """Return the cached DuckLake connection, created once per Streamlit session."""
    return setup_ducklake()


@st.cache_data(ttl="5m")
def load_population_by_province() -> pl.DataFrame:
    """Load `population_by_province`, cached for 5 minutes."""
    conn = get_connection()
    return conn.sql("SELECT * FROM ducklake.main.population_by_province ORDER BY total_population DESC").pl()


@st.cache_data(ttl="5m")
def load_population_by_age_group() -> pl.DataFrame:
    """Load `population_by_age_group`, cached for 5 minutes."""
    conn = get_connection()
    return conn.sql("SELECT * FROM ducklake.main.population_by_age_group ORDER BY total_population DESC").pl()


def _forwarded_identity() -> str | None:
    """Read the display-hint identity oauth2-proxy forwards via Traefik.

    Per ADR-0002/ADR-0008 these X-Auth-Request-* headers are display hints only,
    not something to authorize against — the real gate already ran at the edge
    before this request ever reached the app. Fine for "signed in as", not for
    access decisions.
    """
    headers = {k.lower(): v for k, v in st.context.headers.items()}
    return headers.get("x-auth-request-email") or headers.get("x-auth-request-user")


st.title("Population dashboard")
st.caption("Live from the DuckLake catalog, the same data the Dagster/Polars pipeline and dbt models write to.")

if identity := _forwarded_identity():
    st.sidebar.caption(f"Signed in as {identity}")

provinces = load_population_by_province()
age_groups = load_population_by_age_group()

total_population = int(provinces["total_population"].sum())
top_province = provinces.row(0, named=True)
top_age_group = age_groups.row(0, named=True)

col1, col2, col3 = st.columns(3)
col1.metric("Total population", f"{total_population:,}")
col2.metric("Largest province", top_province["province"], f"{top_province['total_population']:,}")
col3.metric("Largest age group", top_age_group["age_bracket"], f"{top_age_group['total_population']:,}")

left, right = st.columns(2)

with left:
    st.subheader("Population by province")
    st.bar_chart(provinces, x="province", y="total_population", horizontal=True)

with right:
    st.subheader("Population by age bracket")
    st.bar_chart(age_groups, x="age_bracket", y="total_population")

with st.expander("Raw data"):
    st.dataframe(provinces, width="stretch")
    st.dataframe(age_groups, width="stretch")
