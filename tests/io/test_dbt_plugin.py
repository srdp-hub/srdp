import logging
import shutil
import socket
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import duckdb
import pytest
from dbt.adapters.duckdb.plugins import BasePlugin

DOCKER = shutil.which("docker")
pytestmark = pytest.mark.skipif(DOCKER is None, reason="needs Docker for a throwaway Postgres")


def _docker(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run([DOCKER, *args], capture_output=True, check=False)  # noqa: S603 — fixed argv, no shell


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def postgres():
    """Start a throwaway Postgres and yield its container name and host port."""
    name = f"srdp-test-pg-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    started = _docker(
        "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:5432",
        "-e", "POSTGRES_PASSWORD=pw", "postgres:17-alpine",
    )  # fmt: skip
    if started.returncode != 0:
        pytest.skip(f"cannot start Postgres in Docker: {started.stderr.decode().strip()}")
    try:
        deadline = time.monotonic() + 60
        while _docker("exec", name, "pg_isready", "-U", "postgres", "-h", "127.0.0.1").returncode != 0:
            if time.monotonic() > deadline:
                pytest.fail("Postgres did not become ready within 60 s")
            time.sleep(0.5)
        yield name, port
    finally:
        _docker("rm", "-f", name)


@pytest.fixture
def ducklake_env(monkeypatch, postgres, tmp_path):
    """Point the settings at a fresh catalog database, because a catalog keeps the data path it was created with."""
    container, port = postgres
    database = f"ducklake_{uuid.uuid4().hex[:8]}"
    assert _docker("exec", container, "createdb", "-U", "postgres", database).returncode == 0
    monkeypatch.setenv("DUCKLAKE_PG_HOST", "127.0.0.1")
    monkeypatch.setenv("DUCKLAKE_PG_PORT", str(port))
    monkeypatch.setenv("DUCKLAKE_PG_DB", database)
    monkeypatch.setenv("DUCKLAKE_PG_PASSWORD", "pw")
    monkeypatch.setenv("DUCKLAKE_DATA_PATH", str(tmp_path / "lake"))


def test_dbt_connection_gets_the_s3_secret_and_the_attached_lake(monkeypatch, ducklake_env):
    for key, value in {
        "STORAGE_BACKEND": "s3",
        "S3_BUCKET": "lake",
        "S3_PREFIX": "dev",
        "S3_ENDPOINT": "garage:3900",
        "S3_URL_STYLE": "path",
        "S3_REGION": "garage",
        "S3_USE_SSL": "false",
        "S3_KEY_ID": "WRITER",
        "S3_SECRET": "writer-secret",
    }.items():
        monkeypatch.setenv(f"DUCKLAKE_{key}", value)
    plugin = BasePlugin.create("srdp.io.dbt_plugin")
    conn = duckdb.connect()

    plugin.configure_connection(conn)

    [(scope, secret)] = conn.execute("SELECT scope, secret_string FROM duckdb_secrets() WHERE type = 's3'").fetchall()
    assert scope == ["s3://lake/dev/"]
    assert "key_id=WRITER" in secret
    [(data_path,)] = conn.execute("SELECT value FROM ducklake.options() WHERE option_name = 'data_path'").fetchall()
    assert data_path == "s3://lake/dev/"


def test_dbt_connection_on_local_storage_attaches_the_lake_without_a_secret(ducklake_env, tmp_path):
    plugin = BasePlugin.create("srdp.io.dbt_plugin")
    conn = duckdb.connect()

    plugin.configure_connection(conn)

    assert conn.execute("SELECT count(*) FROM duckdb_secrets()").fetchone() == (0,)
    [(data_path,)] = conn.execute("SELECT value FROM ducklake.options() WHERE option_name = 'data_path'").fetchall()
    assert data_path.rstrip("/") == str(tmp_path / "lake")


def test_dbt_connections_racing_to_set_up_a_new_catalog_all_attach(ducklake_env, tmp_path, caplog):
    """A dbt run next to Dagster, or a second dbt run, can make the first ATTACH of a new catalog at the same moment."""
    racers = 4
    start = threading.Barrier(racers)
    conns = [duckdb.connect() for _ in range(racers)]

    def attach(conn: duckdb.DuckDBPyConnection) -> None:
        start.wait()
        BasePlugin.create("srdp.io.dbt_plugin").configure_connection(conn)

    with caplog.at_level(logging.WARNING, logger="srdp.io.ducklake"), ThreadPoolExecutor(racers) as pool:
        list(pool.map(attach, conns))

    assert "init race" in caplog.text, "no connection lost the race, so the retry went untested"
    for conn in conns:
        [(data_path,)] = conn.execute("SELECT value FROM ducklake.options() WHERE option_name = 'data_path'").fetchall()
        assert data_path.rstrip("/") == str(tmp_path / "lake")
