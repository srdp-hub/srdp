import duckdb
import pytest
from pydantic import ValidationError

from srdp.io.ducklake import (
    DuckLakeSettings,
    LocalStorageBackend,
    S3StorageBackend,
    S3StorageSettings,
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


# S3 settings


@pytest.mark.parametrize("field", ["bucket", "endpoint", "region", "key_id", "secret"])
def test_s3_treats_an_empty_setting_as_missing(field):
    """Compose's ``${VAR:-}`` passes an empty string; DuckDB would read it as "use the AWS default"."""
    with pytest.raises(ValidationError) as exc:
        S3StorageSettings(_env_file=None, **{**S3, field: ""})

    assert _invalid_fields(exc) == {field}


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


def test_dlt_gets_the_same_bucket_endpoint_and_key():
    backend = _s3_backend(endpoint="minio:9000", use_ssl=False)

    assert backend.dlt_filesystem_config() == {
        "bucket_url": "s3://lake/dev/",
        "credentials": {
            "aws_access_key_id": "SCWREADER",
            "aws_secret_access_key": "reader-secret",
            "endpoint_url": "http://minio:9000",
            "region_name": "nl-ams",
            "s3_url_style": "path",
        },
    }


def test_a_settings_error_does_not_print_the_secret():
    """The error lands in startup logs, so it must not echo the key it was given."""
    with pytest.raises(ValidationError) as exc:
        S3StorageSettings(_env_file=None, bucket="lake", secret="do-not-log-me")  # noqa: S106 — leak probe

    assert "do-not-log-me" not in str(exc.value)
