"""Tests for ``python -m srdp.setup``: the order of the steps and when it stops."""

import json
import urllib.error
from typing import Any, Self

import pytest

from srdp.setup.__main__ import main

GARAGE_ENV = {
    "SETUP_GARAGE__ENABLED": "true",
    "SETUP_GARAGE__ADMIN_URL": "http://garage.test:3903",
    "SETUP_GARAGE__BUCKET": "ducklake",
    "SETUP_GARAGE__ADMIN_TOKEN": "admin-token",
    "SETUP_GARAGE__WRITER_KEY_ID": "GK" + "a" * 24,
    "SETUP_GARAGE__WRITER_SECRET": "c" * 64,
    "SETUP_GARAGE__READER_KEY_ID": "GK" + "b" * 24,
    "SETUP_GARAGE__READER_SECRET": "d" * 64,
}


class FakeCursor:
    """A cursor on an empty cluster: nothing exists yet."""

    def __init__(self, statements: list[str]) -> None:  # noqa: D107
        self.statements = statements

    def __enter__(self) -> Self:  # noqa: D105
        return self

    def __exit__(self, *_: object) -> None:  # noqa: D105
        pass

    def execute(self, query: object, _params: object = None) -> None:
        """Record the statement."""
        self.statements.append(query if isinstance(query, str) else repr(query))

    def fetchone(self) -> None:
        """Answer every existence check with 'no'."""


class FakeConnection:
    """A Postgres connection that records what setup runs on it."""

    def __init__(self, events: list[str]) -> None:  # noqa: D107
        self.events = events
        self.statements: list[str] = []
        self.autocommit = False

    def cursor(self) -> FakeCursor:
        """Return a recording cursor."""
        return FakeCursor(self.statements)

    def close(self) -> None:
        """Note that the database step is done."""
        self.events.append("postgres closed")


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run setup against a fake Postgres and a Garage that never answers, recording both."""
    recorded: list[str] = []
    monkeypatch.setenv("POSTGRES_PASSWORD", "superuser-pw")
    monkeypatch.setenv("SETUP_DATABASES", json.dumps([{"name": "ducklake"}]))

    def connect(**_: Any) -> FakeConnection:
        recorded.append("postgres connect")
        return FakeConnection(recorded)

    def urlopen(request: Any, **_: Any) -> None:
        recorded.append(f"garage {request.full_url}")
        raise urllib.error.URLError(ConnectionRefusedError("connection refused"))

    monkeypatch.setattr("psycopg2.connect", connect)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("srdp.setup.garage.time.sleep", lambda _: None)
    return recorded


def test_bad_garage_config_stops_setup_before_postgres(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    for name, value in GARAGE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("SETUP_GARAGE__WRITER_KEY_ID", "not-a-garage-key")

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 1
    assert events == []


def test_disabled_garage_step_never_contacts_garage(events: list[str]) -> None:
    main()

    assert events == ["postgres connect", "postgres closed"]


def test_unreachable_garage_fails_after_the_databases_and_names_garage(
    monkeypatch: pytest.MonkeyPatch, events: list[str]
) -> None:
    for name, value in GARAGE_ENV.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match="Garage"):
        main()

    assert events[:2] == ["postgres connect", "postgres closed"]
    assert events[2:]
    assert all(event == "garage http://garage.test:3903/v2/GetClusterStatus" for event in events[2:])
