"""Optional setup step for the bundled Garage S3 server, for DuckLake on S3 in local testing.

Garage stands in for object storage in Docker Compose (the ``s3`` profile) and
in kind (``values-local-s3.yaml``). This makes a fresh single-node Garage
usable and keeps an existing one in line with the configured keys:

- assign the node a layout role, the one-time step a new Garage needs,
- create the DuckLake bucket,
- import the writer and the reader key from their configured id and secret,
- grant the writer read and write, and the reader read only.

The key ids and secrets are the same values the DuckLake consumers get as
``DUCKLAKE_S3_KEY_ID`` and ``DUCKLAKE_S3_SECRET``, so Garage accepts exactly
the keys the deployment hands out. Garage wants an id of ``GK`` plus 24 hex
characters and a secret of 64 hex characters.

This is a step of the setup service (``python -m srdp.setup``), after the
database bootstrap. It only runs when ``[setup.garage]`` has ``enabled = true``
(see ``GarageTarget``): the chart sets it from ``garage.enabled``, Compose from
``SETUP_GARAGE_ENABLED`` in ``.env``. It talks to Garage's admin API v2 with
the standard library only, because the Garage image has no shell.
"""

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

logger = logging.getLogger(__name__)

_NOT_FOUND = 404
REQUEST_TIMEOUT_SECONDS = 10
STATUS_ATTEMPTS = 30
STATUS_RETRY_DELAY_SECONDS = 2.0
# The longest the step waits for Garage to come up. The chart's
# setup.activeDeadlineSeconds must cover it plus the database wait, a test checks.
GARAGE_WAIT_SECONDS = STATUS_ATTEMPTS * (REQUEST_TIMEOUT_SECONDS + STATUS_RETRY_DELAY_SECONDS)
# Garage's own formats for an imported key.
_KEY_ID = re.compile(r"GK[0-9a-f]{24}")
_SECRET = re.compile(r"[0-9a-f]{64}")


class GarageTarget(BaseModel):
    """The ``[setup.garage]`` table of ``srdp.toml``, plus Garage's secrets from the environment.

    ``enabled``, ``admin_url`` and ``bucket`` come from the table. The admin
    token and both keys come from ``SETUP_GARAGE__<FIELD>`` env vars, e.g.
    ``SETUP_GARAGE__WRITER_KEY_ID``. A disabled step ignores everything else,
    so another S3 server's keys, or the empty values Compose passes without
    the ``s3`` profile, never fail validation.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    enabled: bool = False
    admin_url: str = ""
    bucket: str = ""
    admin_token: SecretStr = SecretStr("")
    writer_key_id: str = ""
    writer_secret: SecretStr = SecretStr("")
    reader_key_id: str = ""
    reader_secret: SecretStr = SecretStr("")
    # A single-node layout only needs some capacity; Garage uses it as a weight.
    capacity_bytes: int = 1_000_000_000

    @model_validator(mode="after")
    def _check_enabled(self) -> "GarageTarget":
        if not self.enabled:
            return self
        # Name the env var or table key, never the value: these are secrets.
        problems = [f"Set {name}." for name, value in self._required() if not value]
        for name, key_id in (
            ("SETUP_GARAGE__WRITER_KEY_ID", self.writer_key_id),
            ("SETUP_GARAGE__READER_KEY_ID", self.reader_key_id),
        ):
            if key_id and not _KEY_ID.fullmatch(key_id):
                problems.append(f"{name} must be GK followed by 24 hex characters.")
        for name, secret in (
            ("SETUP_GARAGE__WRITER_SECRET", self.writer_secret),
            ("SETUP_GARAGE__READER_SECRET", self.reader_secret),
        ):
            if secret.get_secret_value() and not _SECRET.fullmatch(secret.get_secret_value()):
                problems.append(f"{name} must be 64 hex characters.")
        if problems:
            msg = "Garage step enabled, but: " + " ".join(problems)
            raise ValueError(msg)
        return self

    def _required(self) -> list[tuple[str, str]]:
        return [
            ("admin_url in [setup.garage]", self.admin_url.strip()),
            ("bucket in [setup.garage]", self.bucket.strip()),
            ("SETUP_GARAGE__ADMIN_TOKEN", self.admin_token.get_secret_value().strip()),
            ("SETUP_GARAGE__WRITER_KEY_ID", self.writer_key_id),
            ("SETUP_GARAGE__WRITER_SECRET", self.writer_secret.get_secret_value()),
            ("SETUP_GARAGE__READER_KEY_ID", self.reader_key_id),
            ("SETUP_GARAGE__READER_SECRET", self.reader_secret.get_secret_value()),
        ]


class GarageAdmin:
    """Minimal client for the Garage admin API v2.

    Args:
        url: Base URL of the admin API, e.g. ``http://garage:3903``.
        token: The admin token from Garage's config.
    """

    def __init__(self, url: str, token: SecretStr) -> None:
        """See class docstring for the arguments."""
        self._url = url.rstrip("/")
        self._token = token

    def call(self, endpoint: str, body: dict[str, Any] | None = None, **query: str) -> dict[str, Any] | None:
        """Call one admin endpoint.

        Args:
            endpoint: Endpoint name, e.g. ``GetClusterStatus``.
            body: JSON body; sends a POST when given, a GET otherwise.
            **query: Query string parameters.

        Returns:
            The decoded JSON response, or ``None`` when Garage answers 404.

        Raises:
            urllib.error.HTTPError: For any other error status.
        """
        url = f"{self._url}/v2/{endpoint}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(  # noqa: S310 -- admin_url is deployment config, not user input
            url,
            data=None if body is None else json.dumps(body).encode(),
            method="GET" if body is None else "POST",
            headers={"Authorization": f"Bearer {self._token.get_secret_value()}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310 -- see above
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code == _NOT_FOUND:
                return None
            detail = exc.read().decode(errors="replace")
            logger.error("Garage %s failed with HTTP %d: %s", endpoint, exc.code, detail)  # noqa: TRY400 -- re-raised below
            raise


def _wait_for_status(
    admin: GarageAdmin,
    max_attempts: int = STATUS_ATTEMPTS,
    delay_seconds: float = STATUS_RETRY_DELAY_SECONDS,
) -> dict[str, Any]:
    """Return the cluster status, retrying while Garage is still starting."""
    for attempt in range(1, max_attempts + 1):
        try:
            status = admin.call("GetClusterStatus")
        except urllib.error.HTTPError:
            # Garage answered, so it is up: a wrong admin token will not fix itself.
            raise
        except (urllib.error.URLError, ConnectionError) as exc:
            logger.warning("Garage not ready yet (attempt %d/%d): %s", attempt, max_attempts, exc)
            time.sleep(delay_seconds)
            continue
        if status is not None:
            return status
    msg = f"Garage admin API did not answer after {max_attempts} attempts"
    raise RuntimeError(msg)


def _ensure_layout(admin: GarageAdmin, status: dict[str, Any], capacity_bytes: int) -> None:
    """Give every node without a role one, and apply the new layout."""
    unassigned = [node["id"] for node in status["nodes"] if node.get("role") is None]
    if not unassigned:
        logger.info("Garage layout already applied (version %d).", status["layoutVersion"])
        return
    roles = [{"id": node_id, "zone": "dc1", "capacity": capacity_bytes, "tags": []} for node_id in unassigned]
    admin.call("UpdateClusterLayout", {"roles": roles})
    admin.call("ApplyClusterLayout", {"version": status["layoutVersion"] + 1})
    logger.info("Applied Garage layout version %d.", status["layoutVersion"] + 1)


def _ensure_bucket(admin: GarageAdmin, bucket: str) -> str:
    """Create the bucket if it does not exist, and return its id."""
    info = admin.call("GetBucketInfo", globalAlias=bucket)
    if info is None:
        info = admin.call("CreateBucket", {"globalAlias": bucket})
        logger.info("Created bucket '%s'.", bucket)
    if info is None:
        msg = f"Garage returned no info for bucket '{bucket}'"
        raise RuntimeError(msg)
    return info["id"]


def _ensure_key(admin: GarageAdmin, name: str, key_id: str, secret: SecretStr) -> None:
    """Import the key unless it exists; refuse an existing key whose secret differs."""
    info = admin.call("GetKeyInfo", id=key_id, showSecretKey="true")
    if info is None:
        admin.call("ImportKey", {"accessKeyId": key_id, "secretAccessKey": secret.get_secret_value(), "name": name})
        logger.info("Imported key '%s' (%s).", name, key_id)
        return
    if info.get("secretAccessKey") != secret.get_secret_value():
        # Garage never lets a key id be reused, so a new secret needs a new id.
        msg = f"Garage key {key_id} exists with a different secret; configure a new key id to rotate it"
        raise RuntimeError(msg)


def _grant(admin: GarageAdmin, bucket_id: str, key_id: str, *, write: bool) -> None:
    """Grant read, plus write when asked, and take away anything more."""
    admin.call(
        "AllowBucketKey",
        {"bucketId": bucket_id, "accessKeyId": key_id, "permissions": {"read": True, "write": write, "owner": False}},
    )
    admin.call(
        "DenyBucketKey",
        {
            "bucketId": bucket_id,
            "accessKeyId": key_id,
            "permissions": {"read": False, "write": not write, "owner": True},
        },
    )


def setup_garage(target: GarageTarget, admin: GarageAdmin | None = None) -> None:
    """Make Garage ready for DuckLake: layout, bucket, and the writer and reader key.

    Args:
        target: The validated, enabled ``[setup.garage]`` config.
        admin: Admin API client; built from ``target`` if not provided.
    """
    if admin is None:
        admin = GarageAdmin(target.admin_url, target.admin_token)
    _ensure_layout(admin, _wait_for_status(admin), target.capacity_bytes)
    bucket_id = _ensure_bucket(admin, target.bucket)
    _ensure_key(admin, "ducklake-writer", target.writer_key_id, target.writer_secret)
    _ensure_key(admin, "ducklake-reader", target.reader_key_id, target.reader_secret)
    _grant(admin, bucket_id, target.writer_key_id, write=True)
    _grant(admin, bucket_id, target.reader_key_id, write=False)
    logger.info(
        "Garage ready: bucket '%s', writer %s, reader %s.",
        target.bucket,
        target.writer_key_id,
        target.reader_key_id,
    )
