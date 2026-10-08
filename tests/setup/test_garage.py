"""Tests for the setup service's Garage step, against an in-memory fake of Garage's admin API."""

import urllib.error
from email.message import Message
from typing import Any

import pytest
from pydantic import SecretStr

from srdp.setup.garage import GarageAdmin, GarageTarget, setup_garage

WRITER_KEY_ID = "GK" + "a" * 24
WRITER_SECRET = "c" * 64
READER_KEY_ID = "GK" + "b" * 24
READER_SECRET = "d" * 64
BUCKET = "ducklake"


class FakeGarage(GarageAdmin):
    """A single-node Garage that keeps its layout, buckets, keys and grants in memory.

    Allow sets each permission flag that is true in the body, Deny clears each
    one that is true, the way Garage's AllowBucketKey and DenyBucketKey do.
    """

    def __init__(self) -> None:  # noqa: D107
        super().__init__("http://garage.test:3903", SecretStr("admin-token"))
        self.nodes: list[dict[str, Any]] = [{"id": "node-1", "role": None}]
        self.layout_version = 0
        self.staged_roles: list[dict[str, Any]] = []
        self.buckets: dict[str, str] = {}
        self.keys: dict[str, str] = {}
        self.grants: dict[tuple[str, str], dict[str, bool]] = {}
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def call(self, endpoint: str, body: dict[str, Any] | None = None, **query: str) -> dict[str, Any] | None:
        """Answer one admin endpoint from the in-memory state."""
        self.calls.append((endpoint, body))
        return getattr(self, f"_{endpoint}")(body or {}, query)

    def _GetClusterStatus(self, _body: dict[str, Any], _query: dict[str, str]) -> dict[str, Any]:  # noqa: N802
        return {"nodes": [dict(node) for node in self.nodes], "layoutVersion": self.layout_version}

    def _UpdateClusterLayout(self, body: dict[str, Any], _query: dict[str, str]) -> dict[str, Any]:  # noqa: N802
        self.staged_roles = body["roles"]
        return {}

    def _ApplyClusterLayout(self, body: dict[str, Any], _query: dict[str, str]) -> dict[str, Any]:  # noqa: N802
        assert body["version"] == self.layout_version + 1, "Garage only applies the next layout version"
        for role in self.staged_roles:
            node = next(node for node in self.nodes if node["id"] == role["id"])
            node["role"] = {"zone": role["zone"], "capacity": role["capacity"]}
        self.layout_version += 1
        return {}

    def _GetBucketInfo(self, _body: dict[str, Any], query: dict[str, str]) -> dict[str, Any] | None:  # noqa: N802
        bucket_id = self.buckets.get(query["globalAlias"])
        return None if bucket_id is None else {"id": bucket_id}

    def _CreateBucket(self, body: dict[str, Any], _query: dict[str, str]) -> dict[str, Any]:  # noqa: N802
        bucket_id = f"bucket-{len(self.buckets) + 1}"
        self.buckets[body["globalAlias"]] = bucket_id
        return {"id": bucket_id}

    def _GetKeyInfo(self, _body: dict[str, Any], query: dict[str, str]) -> dict[str, Any] | None:  # noqa: N802
        secret = self.keys.get(query["id"])
        return None if secret is None else {"accessKeyId": query["id"], "secretAccessKey": secret}

    def _ImportKey(self, body: dict[str, Any], _query: dict[str, str]) -> dict[str, Any]:  # noqa: N802
        self.keys[body["accessKeyId"]] = body["secretAccessKey"]
        return {}

    def _AllowBucketKey(self, body: dict[str, Any], _query: dict[str, str]) -> dict[str, Any]:  # noqa: N802
        grant = self._grant(body)
        grant.update({flag: True for flag, on in body["permissions"].items() if on})
        return {}

    def _DenyBucketKey(self, body: dict[str, Any], _query: dict[str, str]) -> dict[str, Any]:  # noqa: N802
        grant = self._grant(body)
        grant.update({flag: False for flag, on in body["permissions"].items() if on})
        return {}

    def _grant(self, body: dict[str, Any]) -> dict[str, bool]:
        key = (body["bucketId"], body["accessKeyId"])
        return self.grants.setdefault(key, {"read": False, "write": False, "owner": False})

    def grant_for(self, key_id: str) -> dict[str, bool]:
        """Return the permissions this key holds on the DuckLake bucket."""
        return self.grants[(self.buckets[BUCKET], key_id)]


def settings() -> GarageTarget:
    """An enabled Garage step with the test bucket and keys."""
    return GarageTarget(
        enabled=True,
        admin_url="http://garage.test:3903",
        admin_token=SecretStr("admin-token"),
        bucket=BUCKET,
        writer_key_id=WRITER_KEY_ID,
        writer_secret=SecretStr(WRITER_SECRET),
        reader_key_id=READER_KEY_ID,
        reader_secret=SecretStr(READER_SECRET),
    )


def test_fresh_garage_gets_layout_bucket_keys_and_grants() -> None:
    garage = FakeGarage()

    setup_garage(settings(), admin=garage)

    assert all(node["role"] is not None for node in garage.nodes)
    assert garage.layout_version == 1
    assert BUCKET in garage.buckets
    assert garage.keys == {WRITER_KEY_ID: WRITER_SECRET, READER_KEY_ID: READER_SECRET}
    assert garage.grant_for(WRITER_KEY_ID) == {"read": True, "write": True, "owner": False}
    assert garage.grant_for(READER_KEY_ID) == {"read": True, "write": False, "owner": False}


def test_rerun_changes_nothing() -> None:
    garage = FakeGarage()
    setup_garage(settings(), admin=garage)
    garage.calls.clear()

    setup_garage(settings(), admin=garage)

    changing = {"UpdateClusterLayout", "ApplyClusterLayout", "CreateBucket", "ImportKey"}
    assert not [endpoint for endpoint, _ in garage.calls if endpoint in changing]
    assert garage.layout_version == 1
    assert garage.grant_for(READER_KEY_ID) == {"read": True, "write": False, "owner": False}


def test_grants_send_exactly_these_allow_and_deny_payloads() -> None:
    # The grants are the only thing that keeps the reader key from writing.
    garage = FakeGarage()

    setup_garage(settings(), admin=garage)

    bucket_id = garage.buckets[BUCKET]
    grants = [(endpoint, body) for endpoint, body in garage.calls if endpoint in {"AllowBucketKey", "DenyBucketKey"}]
    assert grants == [
        (
            "AllowBucketKey",
            {
                "bucketId": bucket_id,
                "accessKeyId": WRITER_KEY_ID,
                "permissions": {"read": True, "write": True, "owner": False},
            },
        ),
        (
            "DenyBucketKey",
            {
                "bucketId": bucket_id,
                "accessKeyId": WRITER_KEY_ID,
                "permissions": {"read": False, "write": False, "owner": True},
            },
        ),
        (
            "AllowBucketKey",
            {
                "bucketId": bucket_id,
                "accessKeyId": READER_KEY_ID,
                "permissions": {"read": True, "write": False, "owner": False},
            },
        ),
        (
            "DenyBucketKey",
            {
                "bucketId": bucket_id,
                "accessKeyId": READER_KEY_ID,
                "permissions": {"read": False, "write": True, "owner": True},
            },
        ),
    ]


def test_reader_that_could_write_loses_write_and_owner() -> None:
    garage = FakeGarage()
    garage.buckets[BUCKET] = "bucket-1"
    garage.keys[READER_KEY_ID] = READER_SECRET
    garage.grants[("bucket-1", READER_KEY_ID)] = {"read": True, "write": True, "owner": True}

    setup_garage(settings(), admin=garage)

    assert garage.grant_for(READER_KEY_ID) == {"read": True, "write": False, "owner": False}


def test_existing_key_with_another_secret_stops_the_step() -> None:
    garage = FakeGarage()
    garage.keys[WRITER_KEY_ID] = "e" * 64

    with pytest.raises(RuntimeError, match="different secret"):
        setup_garage(settings(), admin=garage)

    assert garage.keys[WRITER_KEY_ID] == "e" * 64


class FailingStatus(FakeGarage):
    """A Garage whose cluster status first fails with the given errors."""

    def __init__(self, *errors: Exception) -> None:  # noqa: D107
        super().__init__()
        self.errors = list(errors)

    def _GetClusterStatus(self, body: dict[str, Any], query: dict[str, str]) -> dict[str, Any]:  # noqa: N802
        if self.errors:
            raise self.errors.pop(0)
        return super()._GetClusterStatus(body, query)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the retry delays instead of waiting them out."""
    delays: list[float] = []
    monkeypatch.setattr("srdp.setup.garage.time.sleep", delays.append)
    return delays


def test_wrong_admin_token_fails_without_retrying(sleeps: list[float]) -> None:
    unauthorized = urllib.error.HTTPError(
        "http://garage.test:3903/v2/GetClusterStatus", 401, "Unauthorized", Message(), None
    )
    garage = FailingStatus(unauthorized)

    with pytest.raises(urllib.error.HTTPError):
        setup_garage(settings(), admin=garage)

    assert sleeps == []
    assert garage.calls == [("GetClusterStatus", None)]


def test_garage_that_comes_up_late_is_waited_for(sleeps: list[float]) -> None:
    refused = urllib.error.URLError(ConnectionRefusedError("connection refused"))
    garage = FailingStatus(refused, refused)

    setup_garage(settings(), admin=garage)

    assert len(sleeps) == 2
    assert garage.grant_for(WRITER_KEY_ID) == {"read": True, "write": True, "owner": False}


def test_garage_that_never_comes_up_gives_up(sleeps: list[float]) -> None:
    refused = urllib.error.URLError(ConnectionRefusedError("connection refused"))
    garage = FailingStatus(*[refused] * 1000)

    with pytest.raises(RuntimeError, match="did not answer"):
        setup_garage(settings(), admin=garage)

    assert sleeps
    assert not [endpoint for endpoint, _ in garage.calls if endpoint != "GetClusterStatus"]
