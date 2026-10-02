"""DuckLake catalog settings, connection management, and Dagster IO manager."""

import logging
import time
from pathlib import Path
from typing import Annotated, Any, Literal

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
from pydantic import Field, SecretStr, StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from srdp.io.storage import StorageBackend

logger = logging.getLogger("srdp.io.ducklake")

_CATALOG = "ducklake"
_DEFAULT_SCHEMA = "main"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class DuckLakeSettings(BaseSettings):
    """DuckLake catalog settings and the choice of storage backend.

    All fields can be overridden via environment variables prefixed
    with ``DUCKLAKE_`` (e.g. ``DUCKLAKE_PG_HOST``). The ``pg_*`` fields
    locate the Postgres catalog. ``storage_backend`` picks where the data
    files live: ``data_path`` for ``local``, ``S3StorageSettings`` for ``s3``.
    """

    model_config = SettingsConfigDict(
        env_prefix="DUCKLAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,  # errors end up in logs and must not echo a password or key
    )

    pg_host: str = Field(default="localhost")
    pg_port: int = Field(default=5432)
    pg_user: str = Field(default="postgres")
    pg_password: str
    pg_db: str = Field(default="ducklake")

    storage_backend: Literal["local", "s3"] = "local"
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


# Compose turns an unset ``${VAR:-}`` into an empty string, which DuckDB would
# read as "use the AWS default". Required S3 settings therefore reject it.
_Required = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class S3StorageSettings(BaseSettings):
    """Where and how to reach the bucket that holds the DuckLake data files.

    Read only when ``DUCKLAKE_STORAGE_BACKEND=s3``. Every field can be set via
    an environment variable prefixed with ``DUCKLAKE_S3_`` (e.g.
    ``DUCKLAKE_S3_BUCKET``). Endpoint, URL style and region have no AWS
    fallback on purpose: AWS defaults do not work against Scaleway, Hetzner
    or MinIO.

    Each process gets one key pair. The deployment decides whether that is a
    read-only key (query-serving apps) or a writer key (Dagster and dbt).
    """

    model_config = SettingsConfigDict(
        env_prefix="DUCKLAKE_S3_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,  # errors end up in logs and must not echo a password or key
    )

    bucket: _Required
    prefix: str = ""
    endpoint: _Required = Field(description="Bare host[:port], e.g. s3.nl-ams.scw.cloud or minio:9000.")
    url_style: Literal["path", "vhost"]
    region: _Required = Field(description="Region used to sign requests, e.g. nl-ams.")
    use_ssl: bool = True
    key_id: _Required
    secret: SecretStr = Field(min_length=1)

    @field_validator("endpoint")
    @classmethod
    def _bare_host(cls, endpoint: str) -> str:
        """Refuse a scheme, because DuckDB builds the URL from ``endpoint`` and ``use_ssl``."""
        if "://" in endpoint:
            msg = "takes a bare host[:port]; choose http or https with DUCKLAKE_S3_USE_SSL"
            raise ValueError(msg)
        return endpoint


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
# Storage backend — S3-compatible object storage
# ---------------------------------------------------------------------------


class S3StorageBackend(StorageBackend):
    """Store DuckLake data files in an S3-compatible bucket.

    Args:
        settings: Bucket, endpoint and key to use.
    """

    def __init__(self, settings: S3StorageSettings) -> None:
        """See class docstring for `settings`."""
        self._s3 = settings

    def get_base_path(self) -> str:
        """Return the ``s3://`` URI of the lake root.

        Returns:
            ``s3://<bucket>/<prefix>/``, or ``s3://<bucket>/`` without a prefix.
        """
        prefix = self._s3.prefix.strip("/")
        path = f"{self._s3.bucket}/{prefix}" if prefix else self._s3.bucket
        return f"s3://{path}/"

    def configure_duckdb(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Load ``httpfs`` and create the S3 secret for this lake.

        The secret is scoped to the lake root, so it is only used for paths
        under ``s3://<bucket>/<prefix>/``.

        Args:
            conn: An open DuckDB connection to configure.
        """
        s3 = self._s3
        conn.execute("INSTALL httpfs")
        conn.execute("LOAD httpfs")
        conn.execute(
            "CREATE OR REPLACE SECRET ducklake_s3 ("
            "TYPE s3, "
            f"KEY_ID {_sql_string(s3.key_id)}, "
            f"SECRET {_sql_string(s3.secret.get_secret_value())}, "
            f"ENDPOINT {_sql_string(s3.endpoint)}, "
            f"URL_STYLE {_sql_string(s3.url_style)}, "
            f"REGION {_sql_string(s3.region)}, "
            f"USE_SSL {str(s3.use_ssl).lower()}, "
            f"SCOPE {_sql_string(self.get_base_path())})"
        )
        logger.info("Configured S3 secret for %s (endpoint=%s).", self.get_base_path(), s3.endpoint)

    def dlt_filesystem_config(self) -> dict[str, Any]:
        """Render these settings as a dlt ``FilesystemConfiguration``.

        Pass the result to ``dlt.destinations.filesystem(**config)`` so a dlt
        pipeline writes with the same bucket, endpoint and key as DuckDB.

        Returns:
            ``bucket_url`` plus ``credentials`` with dlt's ``AwsCredentials`` field names.
        """
        s3 = self._s3
        scheme = "https" if s3.use_ssl else "http"
        return {
            "bucket_url": self.get_base_path(),
            "credentials": {
                "aws_access_key_id": s3.key_id,
                "aws_secret_access_key": s3.secret.get_secret_value(),
                "endpoint_url": f"{scheme}://{s3.endpoint}",
                "region_name": s3.region,
                "s3_url_style": s3.url_style,
            },
        }


def _sql_string(value: str) -> str:
    """Quote ``value`` as a SQL string literal, doubling embedded quotes."""
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


def get_storage_backend(settings: DuckLakeSettings) -> StorageBackend:
    """Build the storage backend that ``settings.storage_backend`` selects.

    For ``s3`` this reads ``S3StorageSettings`` from the ``DUCKLAKE_S3_*``
    environment, so a missing or empty S3 setting fails here, at startup.

    Args:
        settings: DuckLake settings with the storage choice.

    Returns:
        The configured storage backend.

    Raises:
        pydantic.ValidationError: If ``s3`` is selected and its settings are incomplete.
    """
    if settings.storage_backend == "s3":
        return S3StorageBackend(S3StorageSettings())  # ty: ignore[missing-argument]
    return LocalStorageBackend(settings.data_path)


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
        backend = get_storage_backend(settings)

    conn = _prepare_connection(duckdb.connect(), backend)
    attach_query = _attach_sql(settings, backend)

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
                conn = _prepare_connection(duckdb.connect(), backend)
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
        settings = DuckLakeSettings()  # ty: ignore[missing-argument]
    ensure_database(settings)
    return create_connection(settings, get_storage_backend(settings))


def attach_catalog(
    conn: duckdb.DuckDBPyConnection,
    settings: DuckLakeSettings,
) -> None:
    """Configure storage and attach the DuckLake catalog on a connection someone else opened.

    For callers that own the connection, such as the dbt-duckdb plugin in
    ``srdp.io.dbt_plugin``. Code that can open its own connection uses
    ``setup_ducklake`` instead, which also retries a concurrent init race.

    Args:
        conn: An open DuckDB connection.
        settings: DuckLake settings with the catalog and storage details.
    """
    backend = get_storage_backend(settings)
    _prepare_connection(conn, backend)
    conn.execute(_attach_sql(settings, backend))
    _apply_tuning_options(conn, settings)
    logger.info("Attached DuckLake catalog (db=%s, data_path=%s).", settings.pg_db, backend.get_base_path())


def _prepare_connection(conn: duckdb.DuckDBPyConnection, backend: StorageBackend) -> duckdb.DuckDBPyConnection:
    """Load the DuckLake extension and let ``backend`` configure its storage access."""
    conn.execute("INSTALL ducklake")
    conn.execute("LOAD ducklake")
    backend.configure_duckdb(conn)
    return conn


def _attach_sql(settings: DuckLakeSettings, backend: StorageBackend) -> str:
    """Build the ``ATTACH`` statement for the DuckLake catalog on ``backend``."""
    catalog = _sql_string(f"ducklake:postgres:{settings.pg_connection_string}")
    data_path = _sql_string(backend.get_base_path())
    return f"ATTACH {catalog} AS {_CATALOG} (DATA_PATH {data_path}, OVERRIDE_DATA_PATH TRUE)"


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

    Register under the ``"io_manager"`` key, not a custom one, so it's every
    asset's default rather than an opt-in.
    """
    conn = setup_ducklake()
    return DuckLakeIOManager(conn)
