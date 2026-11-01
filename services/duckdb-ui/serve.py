"""Attach the DuckLake catalog and serve DuckDB's built-in UI through the proxy."""

import asyncio
import logging
from pathlib import Path

from srdp.duckdb_ui.proxy import serve
from srdp.io.ducklake import load_extension, setup_ducklake

logging.basicConfig(level=logging.INFO)

conn = setup_ducklake()
# The ui extension makes only the last directory of its data path, and nothing creates ~/.duckdb now that the extensions are baked in.
(Path.home() / ".duckdb" / "extension_data").mkdir(parents=True, exist_ok=True)
load_extension(conn, "ui")
conn.execute("CALL start_ui_server()")

asyncio.run(serve())
