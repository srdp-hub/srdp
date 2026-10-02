"""Render the Compose stack with `docker compose config` and check how DuckLake gets its S3 keys."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "deploy" / "docker" / "docker-compose.yml"
DOCKER = shutil.which("docker")

pytestmark = pytest.mark.skipif(DOCKER is None, reason="needs docker compose")

WRITER_KEY_ID = "GK" + "a" * 24
WRITER_SECRET = "w" * 64
READER_KEY_ID = "GK" + "b" * 24
READER_SECRET = "r" * 64
READERS = ["marimo", "streamlit", "api", "duckdb-ui"]


def render(tmp_path: Path, env: dict[str, str], *profiles: str) -> dict[str, Any]:
    """Return the resolved Compose config for an .env with these variables."""
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{key}={value}\n" for key, value in env.items()))
    args = [DOCKER, "compose", "-f", str(COMPOSE_FILE), "--env-file", str(env_file)]
    for profile in profiles:
        args += ["--profile", profile]
    # Fixed argv, no shell: every argument comes from this test module.
    result = subprocess.run([*args, "config", "--format", "json"], capture_output=True, text=True, check=True)  # noqa: S603
    return json.loads(result.stdout)["services"]


@pytest.fixture
def s3(tmp_path: Path) -> dict[str, Any]:
    """The stack with DUCKLAKE_STORAGE_BACKEND=s3 and the bundled Garage."""
    return render(
        tmp_path,
        {
            "DUCKLAKE_STORAGE_BACKEND": "s3",
            "DUCKLAKE_S3_WRITER_KEY_ID": WRITER_KEY_ID,
            "DUCKLAKE_S3_WRITER_SECRET": WRITER_SECRET,
            "DUCKLAKE_S3_READER_KEY_ID": READER_KEY_ID,
            "DUCKLAKE_S3_READER_SECRET": READER_SECRET,
        },
        "s3",
    )


@pytest.mark.parametrize("name", READERS)
def test_s3_apps_get_only_the_read_only_key(s3: dict[str, Any], name: str) -> None:
    env = s3[name]["environment"]
    assert env["DUCKLAKE_STORAGE_BACKEND"] == "s3"
    assert env["DUCKLAKE_S3_KEY_ID"] == READER_KEY_ID
    assert env["DUCKLAKE_S3_SECRET"] == READER_SECRET
    assert WRITER_SECRET not in json.dumps(s3[name])


def test_s3_dagster_code_gets_the_writer_key(s3: dict[str, Any]) -> None:
    env = s3["dagster-code"]["environment"]
    assert env["DUCKLAKE_STORAGE_BACKEND"] == "s3"
    assert env["DUCKLAKE_S3_KEY_ID"] == WRITER_KEY_ID
    assert env["DUCKLAKE_S3_SECRET"] == WRITER_SECRET


def test_s3_writer_key_reaches_only_dagster_and_the_setup_service(s3: dict[str, Any]) -> None:
    holders = {name for name, service in s3.items() if WRITER_SECRET in json.dumps(service)}
    assert holders == {"dagster-code", "srdp-setup"}


def test_s3_setup_service_waits_for_garage(s3: dict[str, Any]) -> None:
    assert "garage-setup" not in s3
    assert s3["srdp-setup"]["depends_on"]["garage"]["condition"] == "service_healthy"


def test_local_storage_stays_the_default_without_garage(tmp_path: Path) -> None:
    services = render(tmp_path, {})
    assert "garage" not in services
    for name in ["dagster-code", *READERS]:
        assert services[name]["environment"]["DUCKLAKE_STORAGE_BACKEND"] == "local", name
