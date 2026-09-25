"""Attach the DuckLake catalog and start DuckDB's built-in UI server.

The `ui` extension only binds to IPv6 loopback (`::1`), with no config
option for `0.0.0.0`, it is built for local desktop use, and its server
rejects requests that don't look like they came from a direct, unproxied
`localhost` client. `entrypoint.sh` runs an HAProxy in front of it
(`haproxy.cfg`) that both makes it reachable from other containers and
rewrites headers to satisfy that check.
"""

import signal

from srdp.io.ducklake import setup_ducklake

conn = setup_ducklake()
conn.execute("INSTALL ui")
conn.execute("LOAD ui")
conn.execute("CALL start_ui_server()")

signal.pause()
