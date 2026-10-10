import duckdb
import pytest
from pydantic import ValidationError

from srdp.io.ducklake import (
    DuckLakeSettings,
    LocalStorageBackend,
    S3StorageBackend,
    S3StorageSettings,
    _attach_with_retry,
    get_storage_backend,
)

S3 = {
    "bucket": "lake",
    "prefix": "dev",
    "endpoint": "s3.nl-ams.scw.cloud",
    "url_style": "path",
    "region": "nl-ams",
    "key_id": "SCWREADER",
    "secret": "reader-secret",
}


def _settings(**overrides) -> DuckLakeSettings:
    return DuckLakeSettings(_env_file=None, pg_password="pw", **overrides)  # noqa: S106 — throwaway test value


def _s3_backend(**overrides) -> S3StorageBackend:
    return S3StorageBackend(S3StorageSettings(_env_file=None, **{**S3, **overrides}))


def _invalid_fields(exc: pytest.ExceptionInfo[ValidationError]) -> set[str]:
    return {str(error["loc"][0]) for error in exc.value.errors()}


# Backend selection


def test_local_is_the_default_backend(tmp_path):
    backend = get_storage_backend(_settings(data_path=str(tmp_path / "lake")))

    assert isinstance(backend, LocalStorageBackend)
    assert backend.get_base_path() == str(tmp_path / "lake")


def test_s3_backend_reads_its_settings_from_the_ducklake_s3_environment(monkeypatch):
    for key, value in S3.items():
        monkeypatch.setenv(f"DUCKLAKE_S3_{key.upper()}", value)

    backend = get_storage_backend(_settings(storage_backend="s3"))

    assert isinstance(backend, S3StorageBackend)
    assert backend.get_base_path() == "s3://lake/dev/"


def test_s3_backend_refuses_to_start_without_its_settings():
    with pytest.raises(ValidationError) as exc:
        get_storage_backend(_settings(storage_backend="s3"))

    assert _invalid_fields(exc) == {"bucket", "endpoint", "url_style", "region", "key_id", "secret"}


def test_an_empty_storage_backend_in_the_environment_means_local(monkeypatch, tmp_path):
    monkeypatch.setenv("DUCKLAKE_STORAGE_BACKEND", "")

    backend = get_storage_backend(_settings(data_path=str(tmp_path / "lake")))

    assert isinstance(backend, LocalStorageBackend)


# S3 settings


@pytest.mark.parametrize("value", ["", "  "])
@pytest.mark.parametrize("field", ["bucket", "endpoint", "region", "key_id", "secret"])
def test_s3_treats_a_blank_setting_as_missing(field, value):
    """Compose's ``${VAR:-}`` passes an empty string; DuckDB would read it as "use the AWS default"."""
    with pytest.raises(ValidationError) as exc:
        S3StorageSettings(_env_file=None, **{**S3, field: value})

    assert _invalid_fields(exc) == {field}


@pytest.mark.parametrize("field", ["bucket", "endpoint", "url_style", "region", "key_id", "secret"])
def test_s3_treats_an_empty_required_setting_in_the_environment_as_missing(monkeypatch, field):
    for key, value in S3.items():
        monkeypatch.setenv(f"DUCKLAKE_S3_{key.upper()}", value)
    monkeypatch.setenv(f"DUCKLAKE_S3_{field.upper()}", "")

    with pytest.raises(ValidationError) as exc:
        S3StorageSettings(_env_file=None)

    assert _invalid_fields(exc) == {field}


def test_s3_falls_back_to_the_default_for_an_empty_optional_setting_in_the_environment(monkeypatch):
    for key, value in S3.items():
        monkeypatch.setenv(f"DUCKLAKE_S3_{key.upper()}", value)
    monkeypatch.setenv("DUCKLAKE_S3_USE_SSL", "")

    assert S3StorageSettings(_env_file=None).use_ssl is True


def test_s3_secret_is_stripped_like_the_key_id():
    """A pasted ``.env`` line can carry a trailing space or newline, which S3 would reject as a bad signature."""
    settings = S3StorageSettings(_env_file=None, **{**S3, "key_id": " SCWREADER ", "secret": " reader-secret\n"})

    assert settings.key_id == "SCWREADER"
    assert settings.secret.get_secret_value() == "reader-secret"


def test_s3_endpoint_with_a_scheme_is_refused():
    with pytest.raises(ValidationError, match="DUCKLAKE_S3_USE_SSL") as exc:
        S3StorageSettings(_env_file=None, **{**S3, "endpoint": "https://s3.nl-ams.scw.cloud"})

    assert _invalid_fields(exc) == {"endpoint"}


# S3 backend


@pytest.mark.parametrize(
    ("prefix", "base_path"), [("dev", "s3://lake/dev/"), ("", "s3://lake/"), ("/dev/", "s3://lake/dev/")]
)
def test_s3_base_path_is_bucket_and_prefix(prefix, base_path):
    assert _s3_backend(prefix=prefix).get_base_path() == base_path


def _s3_secrets(conn) -> list[dict[str, str]]:
    rows = conn.execute("SELECT secret_string FROM duckdb_secrets() WHERE type = 's3'").fetchall()
    return [dict(pair.split("=", 1) for pair in row[0].split(";")) for row in rows]


def test_s3_connection_gets_a_secret_scoped_to_the_lake():
    conn = duckdb.connect()

    _s3_backend().configure_duckdb(conn)

    [secret] = _s3_secrets(conn)
    assert secret["scope"] == "s3://lake/dev/"
    assert secret["endpoint"] == "s3.nl-ams.scw.cloud"
    assert secret["url_style"] == "path"
    assert secret["region"] == "nl-ams"
    assert secret["use_ssl"] == "true"
    assert secret["key_id"] == "SCWREADER"


def test_a_quote_in_a_credential_cannot_break_out_of_the_secret_sql():
    conn = duckdb.connect()

    _s3_backend(key_id="a'b", secret="x'); DROP TABLE t; --").configure_duckdb(conn)  # noqa: S106 — injection probe

    [secret] = _s3_secrets(conn)
    assert secret["key_id"] == "a'b"


# Catalog-setup race


class _ScriptedConnection:
    """Stands in for a DuckDB connection whose ``ATTACH`` raises the scripted errors, then succeeds."""

    def __init__(self, *errors: duckdb.Error) -> None:
        self._errors = list(errors)
        self.attaches = 0

    def execute(self, query: str) -> None:
        if query.startswith("ATTACH"):
            self.attaches += 1
            if self._errors:
                raise self._errors.pop(0)


def _init_race_error() -> duckdb.Error:
    """The error a real race raises: Postgres refuses the second CREATE TABLE of the catalog's metadata."""
    return duckdb.Error(
        'Failed to initialize DuckLake: Failed to execute query "CREATE TABLE "public"."ducklake_metadata"(...)": '
        "ERROR:  duplicate key value violates unique constraint ... already exists."
    )


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("srdp.io.ducklake.time.sleep", slept.append)
    return slept


def test_an_attach_that_loses_the_catalog_setup_race_is_retried(sleeps):
    conn = _ScriptedConnection(_init_race_error())

    _attach_with_retry(conn, "ATTACH 'ducklake:...' AS ducklake")

    assert conn.attaches == 2
    assert sleeps == [1]


def test_the_attach_retry_gives_up_after_three_attempts(sleeps):
    conn = _ScriptedConnection(_init_race_error(), _init_race_error(), _init_race_error())

    with pytest.raises(duckdb.Error, match="already exists"):
        _attach_with_retry(conn, "ATTACH 'ducklake:...' AS ducklake")

    assert conn.attaches == 3
    assert sleeps == [1, 1]


def test_any_other_attach_error_fails_at_once(sleeps):
    conn = _ScriptedConnection(duckdb.IOException("could not connect to server"))

    with pytest.raises(duckdb.IOException):
        _attach_with_retry(conn, "ATTACH 'ducklake:...' AS ducklake")

    assert conn.attaches == 1
    assert sleeps == []


def test_a_settings_error_does_not_print_the_secret():
    """The error lands in startup logs, so it must not echo the key it was given."""
    with pytest.raises(ValidationError) as exc:
        S3StorageSettings(_env_file=None, bucket="lake", secret="do-not-log-me")  # noqa: S106 — leak probe

    assert "do-not-log-me" not in str(exc.value)
