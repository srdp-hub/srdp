"""Tests for the setup service's database bootstrap config and reconciliation."""

from pathlib import Path

import psycopg2
import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import SettingsConfigDict

from srdp.setup.bootstrap import (
    CONNECT_TIMEOUT_SECONDS,
    DatabaseTarget,
    SetupSettings,
    _connect_with_retry,
    ensure_target,
)

CONFIG_TOML = """
# Tables other than [setup] belong to other consumers and are ignored.
[other]
key = "value"

[[setup.databases]]
name = "zitadel"
role = "zitadel"
enabled = false

[[setup.databases]]
name = "marquez"
role = "marquez"

[[setup.databases]]
name = "ducklake"
"""


class FakeCursor:
    """Records executed statements and answers existence checks from a fixed set."""

    def __init__(self, existing_roles: set[str], existing_databases: set[str]) -> None:  # noqa: D107
        self.existing_roles = existing_roles
        self.existing_databases = existing_databases
        self.statements: list[str] = []
        self._result: tuple[int] | None = None

    def execute(self, query: object, params: tuple[str, ...] | None = None) -> None:
        """Record the statement and prime `fetchone` for existence checks."""
        text = query if isinstance(query, str) else repr(query)
        self.statements.append(text)
        if "pg_roles" in text:
            self._result = (1,) if params and params[0] in self.existing_roles else None
        elif "pg_database" in text:
            self._result = (1,) if params and params[0] in self.existing_databases else None

    def fetchone(self) -> tuple[int] | None:
        """Return the result of the last existence check."""
        return self._result


@pytest.fixture
def settings_from_toml(tmp_path: Path) -> type[SetupSettings]:
    config = tmp_path / "srdp.toml"
    config.write_text(CONFIG_TOML)

    class TomlSettings(SetupSettings):
        model_config = SettingsConfigDict(toml_file=config, toml_table_header=("setup",))

    return TomlSettings


def test_loads_databases_from_toml_and_passwords_from_env(
    settings_from_toml: type[SetupSettings], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SETUP_PASSWORDS__MARQUEZ", "marquez-pw")

    settings = settings_from_toml()  # ty: ignore[missing-argument]

    assert [t.name for t in settings.databases] == ["zitadel", "marquez", "ducklake"]
    assert settings.passwords["marquez"].get_secret_value() == "marquez-pw"


def test_repo_srdp_toml_loads_next_to_its_deploy_table(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_toml = Path(__file__).resolve().parents[2] / "srdp.toml"

    class TomlSettings(SetupSettings):
        model_config = SettingsConfigDict(toml_file=repo_toml, toml_table_header=("setup",))

    for role in ("ZITADEL", "DAGSTER", "MARQUEZ"):
        monkeypatch.setenv(f"SETUP_PASSWORDS__{role}", "pw")

    settings = TomlSettings()  # ty: ignore[missing-argument]

    assert [t.name for t in settings.databases] == ["zitadel", "dagster", "marquez", "ducklake"]


def test_missing_password_for_enabled_role_fails(settings_from_toml: type[SetupSettings]) -> None:
    with pytest.raises(ValidationError, match="SETUP_PASSWORDS__MARQUEZ"):
        settings_from_toml()  # ty: ignore[missing-argument]


def test_empty_password_for_enabled_role_fails(
    settings_from_toml: type[SetupSettings], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SETUP_PASSWORDS__MARQUEZ", "")

    with pytest.raises(ValidationError, match="SETUP_PASSWORDS__MARQUEZ"):
        settings_from_toml()  # ty: ignore[missing-argument]


@pytest.fixture(autouse=True)
def _superuser_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "superuser-pw")
    # The fake cursor has no connection to hash against.
    monkeypatch.setattr("srdp.setup.bootstrap.encrypt_password", lambda pw, *_: f"hashed:{pw}")


@pytest.mark.parametrize("role", ["Dagster", "my-role", "r" * 64])
def test_invalid_role_name_fails(role: str) -> None:
    with pytest.raises(ValidationError, match="pattern"):
        DatabaseTarget(name="x", role=role)


def test_identifiers_up_to_63_chars_pass() -> None:
    target = DatabaseTarget(name="d" * 63, role="r" * 63)
    assert len(target.name) == len(target.role or "") == 63


def test_role_with_double_underscore_finds_its_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SETUP_PASSWORDS__A__B", "pw")
    settings = SetupSettings(databases=[DatabaseTarget(name="x", role="a__b")])  # ty: ignore[missing-argument]
    assert settings.passwords["a__b"].get_secret_value() == "pw"


@pytest.mark.parametrize("name", ["", "Marquez", "my-db", "d" * 64])
def test_invalid_database_name_fails(name: str) -> None:
    with pytest.raises(ValidationError, match="pattern"):
        DatabaseTarget(name=name)


def test_superuser_as_role_fails() -> None:
    with pytest.raises(ValidationError, match="superuser"):
        SetupSettings(  # ty: ignore[missing-argument]
            databases=[DatabaseTarget(name="x", role="postgres")],
            passwords={"postgres": SecretStr("pw")},
        )


def test_misspelled_database_key_fails(tmp_path: Path) -> None:
    config = tmp_path / "srdp.toml"
    config.write_text('[[setup.databases]]\nname = "marquez"\nrol = "marquez"\n')

    class TomlSettings(SetupSettings):
        model_config = SettingsConfigDict(toml_file=config, toml_table_header=("setup",))

    with pytest.raises(ValidationError, match="rol"):
        TomlSettings()  # ty: ignore[missing-argument]


def test_misspelled_setup_key_fails(tmp_path: Path) -> None:
    config = tmp_path / "srdp.toml"
    config.write_text('[setup]\npg_hsot = "db"\n\n[[setup.databases]]\nname = "ducklake"\n')

    class TomlSettings(SetupSettings):
        model_config = SettingsConfigDict(toml_file=config, toml_table_header=("setup",))

    with pytest.raises(ValidationError, match="pg_hsot"):
        TomlSettings()  # ty: ignore[missing-argument]


def test_duplicate_database_names_fail() -> None:
    with pytest.raises(ValidationError, match="Duplicate"):
        SetupSettings(  # ty: ignore[missing-argument]
            databases=[DatabaseTarget(name="ducklake"), DatabaseTarget(name="ducklake")],
        )


def _settings(*targets: DatabaseTarget) -> SetupSettings:
    return SetupSettings(  # ty: ignore[missing-argument]
        databases=list(targets),
        passwords={"marquez": SecretStr("marquez-pw")},
    )


def test_existing_role_gets_password_reconciled() -> None:
    target = DatabaseTarget(name="marquez", role="marquez")
    cur = FakeCursor(existing_roles={"marquez"}, existing_databases={"marquez"})

    ensure_target(cur, target, _settings(target))

    assert any("ALTER ROLE" in s for s in cur.statements)
    assert not any("CREATE" in s for s in cur.statements)


def test_missing_role_and_database_are_created() -> None:
    target = DatabaseTarget(name="marquez", role="marquez")
    cur = FakeCursor(existing_roles=set(), existing_databases=set())

    ensure_target(cur, target, _settings(target))

    assert any("CREATE ROLE" in s for s in cur.statements)
    assert any("CREATE DATABASE" in s for s in cur.statements)


def test_roleless_target_is_owned_by_superuser() -> None:
    target = DatabaseTarget(name="ducklake")
    cur = FakeCursor(existing_roles=set(), existing_databases=set())

    ensure_target(cur, target, _settings(target))

    assert not any("ROLE" in s for s in cur.statements)
    assert any("CREATE DATABASE" in s and "'postgres'" in s for s in cur.statements)


def test_connect_retries_with_a_per_attempt_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hanging attempt must not use up the Job's activeDeadlineSeconds."""
    calls: list[dict[str, object]] = []
    connection = object()

    def fake_connect(**kwargs: object) -> object:
        calls.append(kwargs)
        if len(calls) < 3:
            msg = "starting up"
            raise psycopg2.OperationalError(msg)
        return connection

    monkeypatch.setattr("srdp.setup.bootstrap.psycopg2.connect", fake_connect)
    monkeypatch.setattr("srdp.setup.bootstrap.time.sleep", lambda _: None)

    assert _connect_with_retry(_settings()) is connection
    assert len(calls) == 3
    assert all(call["connect_timeout"] == CONNECT_TIMEOUT_SECONDS for call in calls)


GARAGE_TOML = """
[[setup.databases]]
name = "ducklake"

[setup.garage]
enabled = true
admin_url = "http://garage:3903"
bucket = "ducklake"
"""

GARAGE_SECRETS = {
    "SETUP_GARAGE__ADMIN_TOKEN": "admin-token-9f2c",
    "SETUP_GARAGE__WRITER_SECRET": "c" * 64,
    "SETUP_GARAGE__READER_SECRET": "d" * 64,
}
GARAGE_ENV = {
    **GARAGE_SECRETS,
    "SETUP_GARAGE__WRITER_KEY_ID": "GK" + "a" * 24,
    "SETUP_GARAGE__READER_KEY_ID": "GK" + "b" * 24,
}


def settings_class_for(config: Path) -> type[SetupSettings]:
    """A SetupSettings that reads this TOML file instead of /etc/srdp/srdp.toml."""

    class TomlSettings(SetupSettings):
        model_config = SettingsConfigDict(toml_file=config, toml_table_header=("setup",))

    return TomlSettings


def settings_class(tmp_path: Path, toml: str) -> type[SetupSettings]:
    """A SetupSettings that reads this TOML text instead of /etc/srdp/srdp.toml."""
    config = tmp_path / "srdp.toml"
    config.write_text(toml)
    return settings_class_for(config)


def test_garage_table_and_garage_secrets_from_env_make_one_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in GARAGE_ENV.items():
        monkeypatch.setenv(name, value)

    garage = settings_class(tmp_path, GARAGE_TOML)().garage  # ty: ignore[missing-argument]

    assert garage.enabled is True
    assert garage.admin_url == "http://garage:3903"
    assert garage.bucket == "ducklake"
    assert garage.admin_token.get_secret_value() == "admin-token-9f2c"
    assert garage.writer_key_id == "GK" + "a" * 24
    assert garage.reader_secret.get_secret_value() == "d" * 64


def test_invalid_garage_config_never_shows_a_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in GARAGE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SETUP_GARAGE__WRITER_KEY_ID", "not-a-garage-key")

    with pytest.raises(ValidationError) as exc:
        settings_class(tmp_path, GARAGE_TOML)()  # ty: ignore[missing-argument]

    message = str(exc.value)
    assert "SETUP_GARAGE__WRITER_KEY_ID must be GK followed by 24 hex characters" in message
    # Pydantic shows the start and end of the raw input, which here holds the secrets.
    assert "input_value" not in message
    for secret in GARAGE_SECRETS.values():
        assert secret[:8] not in message
        assert secret[-8:] not in message


def test_unknown_key_in_garage_table_fails(tmp_path: Path) -> None:
    toml = GARAGE_TOML.replace('bucket = "ducklake"', 'bucket = "ducklake"\nadmin_port = 3903')

    with pytest.raises(ValidationError, match="admin_port"):
        settings_class(tmp_path, toml)()  # ty: ignore[missing-argument]


@pytest.mark.parametrize(
    "env",
    [
        # What Compose passes without the s3 profile.
        dict.fromkeys(GARAGE_ENV, ""),
        # Another S3 server's keys, e.g. Scaleway's, which the Garage step never imports.
        {"SETUP_GARAGE__WRITER_KEY_ID": "SCWABCDEFGHIJKLMNOPQ", "SETUP_GARAGE__WRITER_SECRET": "not-hex"},
    ],
)
def test_disabled_garage_step_ignores_garage_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, env: dict[str, str]
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    toml = GARAGE_TOML.replace("enabled = true", "enabled = false")

    assert settings_class(tmp_path, toml)().garage.enabled is False  # ty: ignore[missing-argument]


def test_repo_srdp_toml_keeps_the_garage_step_off(monkeypatch: pytest.MonkeyPatch) -> None:
    repo_toml = Path(__file__).resolve().parents[2] / "srdp.toml"
    for role in ("ZITADEL", "DAGSTER", "MARQUEZ"):
        monkeypatch.setenv(f"SETUP_PASSWORDS__{role}", "pw")

    garage = settings_class_for(repo_toml)().garage  # ty: ignore[missing-argument]

    assert garage.enabled is False
    assert garage.admin_url == "http://garage:3903"
    assert garage.bucket == "ducklake"
