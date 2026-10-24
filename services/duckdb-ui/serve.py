"""Attach the DuckLake catalog and serve DuckDB's built-in UI through the proxy."""

import asyncio
import logging

from srdp.duckdb_ui.proxy import serve
from srdp.io.ducklake import setup_ducklake

logging.basicConfig(level=logging.INFO)

conn = setup_ducklake()
conn.execute("INSTALL ui")
conn.execute("LOAD ui")
conn.execute("CALL start_ui_server()")

asyncio.run(serve())
