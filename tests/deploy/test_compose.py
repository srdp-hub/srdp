"""Render the Compose stack with `docker compose config` and check how DuckLake gets its S3 keys."""

import json
import shutil
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "docker" / "docker-compose.yml"
DOCKER = shutil.which("docker")
# Required by docker-compose.yml, the just recipes pass it the same way.
DOMAIN = tomllib.loads((REPO_ROOT / "srdp.toml").read_text())["deploy"]["domain"]

pytestmark = pytest.mark.skipif(DOCKER is None, reason="needs docker compose")

WRITER_KEY_ID = "GK" + "a" * 24
WRITER_SECRET = "w" * 64
READER_KEY_ID = "GK" + "b" * 24
READER_SECRET = "r" * 64
READERS = ["marimo", "streamlit", "api", "duckdb-ui"]


def render_project(tmp_path: Path, env: dict[str, str], *profiles: str) -> dict[str, Any]:
    """Return the whole resolved Compose project for an .env with these variables."""
    env_file = tmp_path / ".env"
    env_file.write_text("".join(f"{key}={value}\n" for key, value in {"SRDP_DOMAIN": DOMAIN, **env}.items()))
    args = [DOCKER, "compose", "-f", str(COMPOSE_FILE), "--env-file", str(env_file)]
    for profile in profiles:
        args += ["--profile", profile]
    # Fixed argv, no shell: every argument comes from this test module.
    result = subprocess.run([*args, "config", "--format", "json"], capture_output=True, text=True, check=True)  # noqa: S603
    return json.loads(result.stdout)


def render(tmp_path: Path, env: dict[str, str], *profiles: str) -> dict[str, Any]:
    """Return the resolved Compose services for an .env with these variables."""
    return render_project(tmp_path, env, *profiles)["services"]


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


GARAGE_ENV = {
    "DUCKLAKE_STORAGE_BACKEND": "s3",
    "DUCKLAKE_S3_BUCKET": "lake",
    "DUCKLAKE_S3_WRITER_KEY_ID": WRITER_KEY_ID,
    "DUCKLAKE_S3_WRITER_SECRET": WRITER_SECRET,
    "DUCKLAKE_S3_READER_KEY_ID": READER_KEY_ID,
    "DUCKLAKE_S3_READER_SECRET": READER_SECRET,
    "GARAGE_ADMIN_TOKEN": "admin-token",
}


def test_setup_garage_enabled_turns_on_the_garage_step_with_the_consumers_keys(tmp_path: Path) -> None:
    env = render(tmp_path, {**GARAGE_ENV, "SETUP_GARAGE_ENABLED": "true"}, "s3")["srdp-setup"]["environment"]
    assert env["SETUP_GARAGE__ENABLED"] == "true"
    assert env["SETUP_GARAGE__BUCKET"] == "lake"
    assert env["SETUP_GARAGE__ADMIN_TOKEN"] == GARAGE_ENV["GARAGE_ADMIN_TOKEN"]
    assert env["SETUP_GARAGE__WRITER_KEY_ID"] == WRITER_KEY_ID
    assert env["SETUP_GARAGE__WRITER_SECRET"] == WRITER_SECRET
    assert env["SETUP_GARAGE__READER_KEY_ID"] == READER_KEY_ID
    assert env["SETUP_GARAGE__READER_SECRET"] == READER_SECRET
    assert not [name for name in env if name.startswith("GARAGE_")]


def test_admin_token_alone_leaves_the_garage_step_off(tmp_path: Path) -> None:
    env = render(tmp_path, GARAGE_ENV, "s3")["srdp-setup"]["environment"]
    assert env["SETUP_GARAGE__ENABLED"] == "false"


def test_garage_runs_hardened(s3: dict[str, Any]) -> None:
    garage = s3["garage"]
    assert garage["read_only"] is True
    assert garage["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in garage["security_opt"]


def test_garage_signs_with_the_region_the_consumers_use(s3: dict[str, Any]) -> None:
    # DuckDB signs every request with DUCKLAKE_S3_REGION, and Garage refuses a signature for another region.
    [mount] = s3["garage"]["configs"]
    assert mount["target"] == "/etc/garage.toml"
    garage_toml = tomllib.loads((COMPOSE_FILE.parent / "garage.toml").read_text())
    assert garage_toml["s3_api"]["s3_region"] == s3["dagster-code"]["environment"]["DUCKLAKE_S3_REGION"]


@pytest.mark.parametrize("name", READERS)
def test_apps_attach_the_catalog_read_only(tmp_path: Path, name: str) -> None:
    """Every app that reads the catalog attaches it read-only."""
    assert render(tmp_path, {})[name]["environment"]["DUCKLAKE_READ_ONLY"] == "true"


@pytest.mark.parametrize(("env", "required"), [({}, "true"), ({"SRDP_DEV_PASSWORD_CHANGE_REQUIRED": "false"}, "false")])
def test_first_admin_must_change_password(tmp_path: Path, env: dict[str, str], required: str) -> None:
    """Compose forces the change at first login, and only the dev recipe turns it off."""
    zitadel = render(tmp_path, env)["zitadel"]["environment"]
    assert zitadel["ZITADEL_FIRSTINSTANCE_ORG_HUMAN_PASSWORDCHANGEREQUIRED"] == required
