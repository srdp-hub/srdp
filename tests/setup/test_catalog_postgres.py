"""The catalog step against a real Postgres: the reader can read the catalog and cannot change it."""

from pathlib import Path
from typing import TYPE_CHECKING

import duckdb
import psycopg2
import pytest
from pydantic import SecretStr

from srdp.io.ducklake import DuckLakeSettings, setup_ducklake
from srdp.setup.bootstrap import DatabaseTarget, SetupSettings, bootstrap_databases
from srdp.setup.catalog import setup_catalogs

if TYPE_CHECKING:
    from tests.conftest import Postgres

# A space, a quote and a backslash, which the libpq connection string must keep intact.
WRITER_PW = "wr ite'r\\pw"
READER_PW = "re ad'er\\pw"


@pytest.fixture(scope="module")
def catalog(postgres: "Postgres", tmp_path_factory: pytest.TempPathFactory) -> tuple["Postgres", Path]:
    """Run the setup bootstrap and the catalog step once, and return the data path."""
    data_path = tmp_path_factory.mktemp("lake")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("POSTGRES_PASSWORD", "pw")
        mp.setenv("DUCKLAKE_DATA_PATH", str(data_path))
        settings = SetupSettings(  # ty: ignore[missing-argument]
            pg_host="127.0.0.1",
            pg_port=postgres.port,
            databases=[DatabaseTarget(name="ducklake", role="ducklake_writer", reader_role="ducklake_reader")],
            passwords={"ducklake_writer": SecretStr(WRITER_PW), "ducklake_reader": SecretStr(READER_PW)},
        )
        bootstrap_databases(settings)
        setup_catalogs(settings)
    return postgres, data_path


def _connect(catalog: tuple["Postgres", Path], user: str, password: str) -> duckdb.DuckDBPyConnection:
    postgres, data_path = catalog
    return setup_ducklake(
        DuckLakeSettings(
            _env_file=None,
            pg_host="127.0.0.1",
            pg_port=postgres.port,
            pg_user=user,
            pg_password=password,
            pg_db="ducklake",
            data_path=str(data_path),
        )
    )


def test_reader_reads_what_the_writer_wrote(catalog: tuple["Postgres", Path]) -> None:
    """A table the writer creates after setup is readable through the reader role."""
    writer = _connect(catalog, "ducklake_writer", WRITER_PW)
    writer.execute("CREATE TABLE ducklake.main.towns AS SELECT 'Delft' AS name")

    reader = _connect(catalog, "ducklake_reader", READER_PW)

    assert reader.execute("SELECT name FROM ducklake.main.towns").fetchall() == [("Delft",)]


def test_reader_cannot_create_a_table(catalog: tuple["Postgres", Path]) -> None:
    """Through DuckLake, a CREATE TABLE as the reader fails on the catalog."""
    reader = _connect(catalog, "ducklake_reader", READER_PW)

    with pytest.raises(duckdb.Error, match="permission denied"):
        reader.execute("CREATE TABLE ducklake.main.nope AS SELECT 1 AS x")


def test_reader_cannot_change_the_catalog_tables(catalog: tuple["Postgres", Path]) -> None:
    """Straight on Postgres, the reader cannot create, change or drop a table."""
    postgres, _ = catalog
    conn = psycopg2.connect(
        host="127.0.0.1", port=postgres.port, user="ducklake_reader", password=READER_PW, dbname="ducklake"
    )
    conn.autocommit = True
    try:
        for statement in (
            "CREATE TABLE public.nope (x int)",
            "DELETE FROM public.ducklake_metadata",
            "DROP TABLE public.ducklake_metadata",
        ):
            with conn.cursor() as cur, pytest.raises(psycopg2.Error, match=r"permission denied|must be owner"):
                cur.execute(statement)
    finally:
        conn.close()
