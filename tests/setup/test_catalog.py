"""Tests for the catalog step of setup: grants for the reader role and the first attach."""

from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr

from srdp.setup import catalog
from srdp.setup.bootstrap import DatabaseTarget, SetupSettings

if TYPE_CHECKING:
    from srdp.io.ducklake import DuckLakeSettings

TARGET = DatabaseTarget(name="ducklake", role="ducklake_writer", reader_role="ducklake_reader")


class FakeCursor:
    """Records statements and answers the ownership queries from a fixed list of tables."""

    def __init__(self, tables: list[str] | None = None) -> None:  # noqa: D107
        self.tables = tables or []
        self.statements: list[str] = []
        self._rows: list[tuple[str]] = []

    def execute(self, query: object, _params: object = None) -> None:
        """Record the statement and prime the rows for the ownership query on tables."""
        text = query if isinstance(query, str) else repr(query)
        self.statements.append(text)
        self._rows = [(name,) for name in self.tables] if "pg_tables" in text else []

    def fetchall(self) -> list[tuple[str]]:
        """Return the rows of the last query."""
        return self._rows


@pytest.fixture(autouse=True)
def _superuser_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_PASSWORD", "superuser-pw")


def _settings(*targets: DatabaseTarget, manage: bool = True) -> SetupSettings:
    return SetupSettings(  # ty: ignore[missing-argument]
        databases=list(targets),
        manage_databases=manage,
        passwords={"ducklake_writer": SecretStr("writer-pw"), "ducklake_reader": SecretStr("reader-pw")},
    )


def test_reader_gets_select_and_nothing_else() -> None:
    """Every statement that names the reader either takes rights away or grants USAGE or SELECT."""
    cur = FakeCursor()

    catalog.reconcile_catalog_access(cur, TARGET)

    for statement in (s for s in cur.statements if "ducklake_reader" in s):
        assert "REVOKE ALL" in statement or "GRANT USAGE ON SCHEMA" in statement or "GRANT SELECT" in statement
    assert not any(word in s for s in cur.statements for word in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"))


def test_later_tables_of_the_owner_are_readable() -> None:
    """DuckLake creates tables at run time, so the default privileges name the owner role."""
    cur = FakeCursor()

    catalog.reconcile_catalog_access(cur, TARGET)

    assert any("ALTER DEFAULT PRIVILEGES FOR ROLE" in s and "ducklake_writer" in s for s in cur.statements)


def test_public_cannot_create_in_the_schema() -> None:
    """Before Postgres 15 every role can create tables in public, so setup revokes it."""
    cur = FakeCursor()

    catalog.reconcile_catalog_access(cur, TARGET)

    assert any("REVOKE CREATE ON SCHEMA" in s and "PUBLIC" in s for s in cur.statements)


def test_tables_of_the_superuser_go_to_the_owner() -> None:
    """A catalog from before the role split is owned by the superuser, so the owner role takes it over."""
    cur = FakeCursor(tables=["ducklake_metadata", "ducklake_snapshot"])

    catalog.reconcile_catalog_access(cur, TARGET)

    handed = [s for s in cur.statements if "ALTER" in s and "OWNER TO" in s and "TABLE" in s]
    assert len(handed) == 2


def test_a_target_without_a_reader_is_refused() -> None:
    """The access step has nothing to grant without a reader role."""
    with pytest.raises(ValueError, match="reader_role"):
        catalog.reconcile_catalog_access(FakeCursor(), DatabaseTarget(name="ducklake", role="ducklake_writer"))


def test_catalog_is_created_as_the_owner_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first attach runs as the owner role, because the reader cannot create tables."""
    seen: list[DuckLakeSettings] = []
    monkeypatch.setattr(catalog, "initialize_catalog", seen.append)

    catalog.create_catalog(_settings(TARGET), TARGET)

    assert [(s.pg_user, s.pg_password, s.pg_db) for s in seen] == [("ducklake_writer", "writer-pw", "ducklake")]


def test_unmanaged_databases_only_get_the_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """With manage_databases off the operator owns the grants, so setup does not connect to change them."""
    created: list[str] = []
    monkeypatch.setattr(catalog, "create_catalog", lambda _settings, target: created.append(target.name))
    monkeypatch.setattr(catalog, "_connect_with_retry", lambda *_a, **_k: pytest.fail("connected"))

    catalog.setup_catalogs(_settings(TARGET, manage=False))

    assert created == ["ducklake"]


def test_targets_without_a_reader_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a catalog target has a reader role, so the others never reach this step."""
    monkeypatch.setattr(catalog, "create_catalog", lambda *_a: pytest.fail("created"))
    monkeypatch.setattr(catalog, "_connect_with_retry", lambda *_a, **_k: pytest.fail("connected"))

    catalog.setup_catalogs(_settings(DatabaseTarget(name="marquez", role="ducklake_writer")))
