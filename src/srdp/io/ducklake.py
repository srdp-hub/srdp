"""DuckLake catalog settings, connection management, and Dagster IO manager."""

import logging
import time
from pathlib import Path

import duckdb
import polars as pl
import psycopg2
from dagster import (
    InitResourceContext,
    InputContext,
    IOManager,
    MetadataValue,
    OutputContext,
    TableColumn,
    TableSchema,
    io_manager,
)
from psycopg2 import sql
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from srdp.io.storage import StorageBackend

logger = logging.getLogger("srdp.io.ducklake")

_CATALOG = "ducklake"
_DEFAULT_SCHEMA = "main"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class DuckLakeSettings(BaseSettings):
    """DuckLake catalog settings.

    All fields can be overridden via environment variables prefixed
    with ``DUCKLAKE_`` (e.g. ``DUCKLAKE_PG_HOST``).
    """

    model_config = SettingsConfigDict(
        env_prefix="DUCKLAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    pg_host: str = Field(default="localhost")
    pg_port: int = Field(default=5432)
    pg_user: str = Field(default="postgres")
    pg_password: str
    pg_db: str = Field(default="ducklake")
    data_path: str = Field(default=".data/ducklake")

    target_file_size: int | None = None
    parquet_compression: str | None = None
    per_thread_output: bool | None = None

    @property
    def pg_connection_string(self) -> str:
        """Build the libpq connection string for DuckLake metadata.

        Returns:
            A space-separated libpq keyword/value connection string.
        """
        return (
            f"host={self.pg_host} port={self.pg_port} "
            f"dbname={self.pg_db} user={self.pg_user} password={self.pg_password}"
        )


# ---------------------------------------------------------------------------
# Storage backend — local filesystem
# ---------------------------------------------------------------------------


class LocalStorageBackend(StorageBackend):
    """Store DuckLake data files on the local filesystem.

    Args:
        base_path: Root directory for file storage. Created automatically
            if it does not exist.
    """

    def __init__(self, base_path: str) -> None:
        """See class docstring for `base_path`."""
        self._base_path = Path(base_path).resolve()
        self._base_path.mkdir(parents=True, exist_ok=True)

    def get_base_path(self) -> str:
        """Return the absolute path to the storage root directory.

        Returns:
            Absolute filesystem path used as DuckLake's DATA_PATH.
        """
        return str(self._base_path)

    def configure_duckdb(self, conn: duckdb.DuckDBPyConnection) -> None:
        """No-op — local filesystem needs no extra DuckDB configuration.

        Args:
            conn: An open DuckDB connection (unused for local storage).
        """


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def ensure_database(settings: DuckLakeSettings) -> None:
    """Create the DuckLake PostgreSQL metadata database if it does not exist.

    Connects to the default ``postgres`` database to check for and optionally
    create the target database. Uses autocommit because ``CREATE DATABASE``
    cannot run inside a transaction.

    Args:
        settings: DuckLake settings with PostgreSQL connection details.
    """
    conn = psycopg2.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        user=settings.pg_user,
        password=settings.pg_password,
        dbname="postgres",
    )
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (settings.pg_db,))
            if cur.fetchone():
                logger.info("DuckLake database '%s' already exists.", settings.pg_db)
                return
            cur.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(settings.pg_db)),
            )

            logger.info("Created DuckLake database '%s'.", settings.pg_db)
    finally:
        conn.close()


def _apply_tuning_options(conn: duckdb.DuckDBPyConnection, settings: DuckLakeSettings) -> None:
    tuning: list[tuple[str, int | str | bool]] = []
    if settings.target_file_size is not None:
        tuning.append(("target_file_size", settings.target_file_size))
    if settings.parquet_compression is not None:
        tuning.append(("parquet_compression", settings.parquet_compression))
    if settings.per_thread_output is not None:
        tuning.append(("per_thread_output", settings.per_thread_output))

    for option, value in tuning:
        if isinstance(value, str):
            sql_value = f"'{value}'"
        elif isinstance(value, bool):
            sql_value = str(value).lower()
        else:
            sql_value = str(value)
        conn.execute(f"CALL ducklake_set_option('ducklake', '{option}', {sql_value})")
        logger.info("DuckLake option %s = %s", option, value)


def create_connection(
    settings: DuckLakeSettings,
    backend: StorageBackend | None = None,
) -> duckdb.DuckDBPyConnection:
    """Create a DuckDB connection with the DuckLake catalog attached.

    Installs and loads the DuckLake extension, applies storage-specific DuckDB
    configuration, and attaches the catalog with retry logic to handle
    concurrent init races.

    Args:
        settings: DuckLake settings with PostgreSQL connection details.
        backend: Storage backend to use. Resolved from ``settings`` if not provided.

    Returns:
        A ready-to-use DuckDB connection with the ``ducklake`` catalog attached.

    Raises:
        duckdb.Error: If the catalog cannot be attached after all retries.
    """
    if backend is None:
        backend = LocalStorageBackend(settings.data_path)

    conn = duckdb.connect()
    conn.execute("INSTALL ducklake")
    conn.execute("LOAD ducklake")
    backend.configure_duckdb(conn)

    attach_query = (
        f"ATTACH 'ducklake:postgres:{settings.pg_connection_string}' "
        f"AS {_CATALOG} (DATA_PATH '{backend.get_base_path()}', OVERRIDE_DATA_PATH TRUE)"
    )

    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn.execute(attach_query)
            break
        except duckdb.Error as exc:
            if "already exists" in str(exc) and attempt < max_retries - 1:
                logger.warning("DuckLake catalog init race (attempt %d), retrying...", attempt + 1)
                time.sleep(1)
                conn.close()
                conn = duckdb.connect()
                conn.execute("INSTALL ducklake")
                conn.execute("LOAD ducklake")
                backend.configure_duckdb(conn)
            else:
                raise

    _apply_tuning_options(conn, settings)
    logger.info(
        "Attached DuckLake catalog (db=%s, data_path=%s).",
        settings.pg_db,
        backend.get_base_path(),
    )
    return conn


def setup_ducklake(
    settings: DuckLakeSettings | None = None,
) -> duckdb.DuckDBPyConnection:
    """Full DuckLake setup: ensure the metadata database exists, then connect.

    This is the main entry point — call it from the Dagster IO manager
    factory or from a CLI init command.

    Args:
        settings: Loaded from environment variables if not provided.

    Returns:
        A ready-to-use DuckDB connection with the ``ducklake`` catalog attached.
    """
    if settings is None:
        settings = DuckLakeSettings()  # type: ignore[call-arg]
    ensure_database(settings)
    backend = LocalStorageBackend(settings.data_path)
    return create_connection(settings, backend)


# ---------------------------------------------------------------------------
# Dagster IO manager
# ---------------------------------------------------------------------------


def asset_key_path_to_table_ref(asset_key_path: list[str]) -> str:
    """Derive a fully-qualified DuckLake table reference from an asset key path.

    The single naming convention every DuckLake consumer (the IO manager, the
    OpenLineage bridge) must share:

    - ``["orders"]``              → ``ducklake.main.orders``
    - ``["raw", "orders"]``       → ``ducklake.raw.orders``
    - ``["raw", "eu", "orders"]`` → ``ducklake.raw.eu_orders``

    Args:
        asset_key_path: Asset key path segments, e.g. ``["raw", "orders"]``.

    Returns:
        Fully-qualified reference like ``ducklake.raw.orders``.
    """
    if len(asset_key_path) == 1:
        schema, table = _DEFAULT_SCHEMA, asset_key_path[0]
    else:
        schema = asset_key_path[0]
        table = "_".join(asset_key_path[1:])
    return f"{_CATALOG}.{schema}.{table}"


class DuckLakeIOManager(IOManager):
    """Dagster IO manager that persists asset outputs as DuckLake tables.

    Asset key path segments are resolved to a fully-qualified table reference:

    - ``["orders"]``              → ``ducklake.main.orders``
    - ``["raw", "orders"]``       → ``ducklake.raw.orders``
    - ``["raw", "eu", "orders"]`` → ``ducklake.raw.eu_orders``

    Outputs are written as ``CREATE OR REPLACE TABLE``.
    Inputs are returned as ``pl.LazyFrame`` — operations are pushed down
    to DuckDB at ``.collect()`` time, enabling DuckLake file pruning.
    """

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Wrap an already-connected DuckDB connection with the `ducklake` catalog attached."""
        self._conn = conn

    def _table_ref(self, asset_key_path: list[str]) -> str:
        return asset_key_path_to_table_ref(asset_key_path)

    def _ensure_schema(self, schema: str) -> None:
        """Create the DuckLake schema if it does not already exist.

        Args:
            schema: Schema name to create inside the ``ducklake`` catalog.
        """
        self._conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_CATALOG}.{schema}")

    def handle_output(self, context: OutputContext, obj: pl.DataFrame | pl.LazyFrame) -> None:
        """Write a Polars DataFrame or LazyFrame to a DuckLake table.

        Accepts both eager and lazy frames. When a ``pl.LazyFrame`` is provided,
        DuckDB evaluates the query plan internally, so data never fully
        materialises in Python memory.

        Attaches schema and row-count metadata to the output automatically, for
        every asset that goes through this IO manager, regardless of what the
        asset function itself returns — this is the "automatic" half of the
        platform's lineage/observability story; column lineage is the opt-in half
        (see ``srdp.lineage``), since it needs the producing expressions, which
        this method never sees, only the resulting frame.

        Args:
            context: Dagster output context containing the asset key.
            obj: The Polars DataFrame or LazyFrame to persist.
        """
        path = list(context.asset_key.path)
        ref = self._table_ref(path)
        schema = ref.split(".")[1]
        self._ensure_schema(schema)
        self._conn.register("_data", obj)
        self._conn.execute(f"CREATE OR REPLACE TABLE {ref} AS SELECT * FROM _data")  # noqa: S608
        self._conn.unregister("_data")

        described = self._conn.sql(f"DESCRIBE {ref}").fetchall()
        row_count = self._conn.sql(f"SELECT COUNT(*) FROM {ref}").fetchone()  # noqa: S608
        row_count = row_count[0] if row_count else 0

        column_names = [col_name for col_name, *_ in described]
        quoted = [name.replace('"', '""') for name in column_names]
        null_count_exprs = ", ".join(f'COUNT(*) FILTER (WHERE "{q}" IS NULL)' for q in quoted)
        null_counts_row = self._conn.sql(f"SELECT {null_count_exprs} FROM {ref}").fetchone()  # noqa: S608
        null_counts = dict(zip(column_names, null_counts_row, strict=True)) if null_counts_row else {}

        context.add_output_metadata(
            {
                "dagster/row_count": row_count,
                "dagster/column_schema": TableSchema(
                    columns=[TableColumn(name=col_name, type=col_type) for col_name, col_type, *_ in described],
                ),
                "null_counts": MetadataValue.json(null_counts),
            },
        )
        logger.info("Wrote %d rows to %s.", row_count, ref)

    def load_input(self, context: InputContext) -> pl.LazyFrame:
        """Load a DuckLake table as a Polars LazyFrame.

        Uses DuckDB's ``pl(lazy=True)`` to return a lazy frame with projection
        and filter pushdown support. Operations chained on the result are pushed
        down to DuckDB at ``.collect()`` time, enabling DuckLake file pruning.

        Args:
            context: Dagster input context containing the upstream asset key.

        Returns:
            A lazy view of the table. Call ``.collect()`` to materialise.
        """
        ref = self._table_ref(list(context.asset_key.path))
        result = self._conn.sql(f"SELECT * FROM {ref}").pl(lazy=True)  # noqa: S608
        logger.info("Loaded lazy frame from %s.", ref)
        return result


@io_manager
def ducklake_io_manager(_init_context: InitResourceContext) -> DuckLakeIOManager:
    """Dagster IO manager factory — sets up DuckLake and returns the IO manager.

    Reads configuration from ``DUCKLAKE_*`` environment variables.
    """
    conn = setup_ducklake()
    return DuckLakeIOManager(conn)
