#!/bin/sh
set -e

python -u serve.py &

# Wait for DuckDB's server to actually bind ::1:4213 before starting HAProxy.
# A fixed sleep isn't reliable here: extension init time varies, and if
# HAProxy's wildcard bind on *:4213 wins the race, DuckDB's own bind to
# ::1:4213 silently fails inside a background thread with no visible error,
# and never recovers (confirmed empirically).
python -c "
import socket, sys, time
for _ in range(60):
    try:
        socket.create_connection(('::1', 4213), timeout=1).close()
        break
    except OSError:
        time.sleep(1)
else:
    sys.exit('DuckDB UI server did not come up within 60s')
"

exec haproxy -f haproxy.cfg
