import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

DOCKER = shutil.which("docker")


@pytest.fixture(autouse=True)
def _clean_ducklake_env(monkeypatch, tmp_path):
    """Keep the developer's own DUCKLAKE_* variables and any .env file out of the settings under test."""
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("DUCKLAKE_"):
            monkeypatch.delenv(name)


def docker(*args: str) -> subprocess.CompletedProcess[bytes]:
    """Run the docker CLI with fixed arguments and capture its output."""
    if DOCKER is None:
        pytest.skip("needs Docker for a throwaway Postgres")
    return subprocess.run([DOCKER, *args], capture_output=True, check=False)  # noqa: S603


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass(frozen=True)
class Postgres:
    """A throwaway Postgres container, reachable on 127.0.0.1 at ``port`` as ``postgres`` with password ``pw``."""

    name: str
    port: int

    def createdb(self, database: str) -> None:
        """Create an empty database."""
        assert docker("exec", self.name, "createdb", "-U", "postgres", database).returncode == 0


@pytest.fixture(scope="module")
def postgres() -> Iterator[Postgres]:
    """Start a throwaway Postgres for the tests of one module."""
    name = f"srdp-test-pg-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    started = docker(
        "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1:{port}:5432",
        "-e", "POSTGRES_PASSWORD=pw", "postgres:18-alpine",
    )  # fmt: skip
    if started.returncode != 0:
        pytest.skip(f"cannot start Postgres in Docker: {started.stderr.decode().strip()}")
    try:
        deadline = time.monotonic() + 60
        while docker("exec", name, "pg_isready", "-U", "postgres", "-h", "127.0.0.1").returncode != 0:
            if time.monotonic() > deadline:
                pytest.fail("Postgres did not become ready within 60 s")
            time.sleep(0.5)
        yield Postgres(name, port)
    finally:
        docker("rm", "-f", name)
