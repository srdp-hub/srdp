"""dbt-duckdb plugin that connects dbt to DuckLake through the platform's storage backend.

Reference it from a dbt profile, so the profile holds no storage settings of
its own::

    outputs:
      dev:
        type: duckdb
        path: ":memory:"
        plugins:
          - module: srdp.io.dbt_plugin

The catalog, the data path and the S3 secret all come from the same
``DUCKLAKE_*`` settings that Dagster and the apps read.
"""

import duckdb
from dbt.adapters.duckdb.plugins import BasePlugin

from srdp.io.ducklake import DuckLakeSettings, attach_catalog


class Plugin(BasePlugin):
    """Attach the DuckLake catalog, on the configured storage, to every dbt connection."""

    def configure_connection(self, conn: duckdb.DuckDBPyConnection) -> None:
        """Configure storage and attach the ``ducklake`` catalog.

        Args:
            conn: The connection dbt-duckdb just opened.
        """
        attach_catalog(conn, DuckLakeSettings())  # ty: ignore[missing-argument]
