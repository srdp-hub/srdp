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
database bootstrap. It only runs when ``GARAGE_ADMIN_TOKEN`` is set: the chart
sets it with ``garage.enabled``, Compose when ``.env`` fills it in for the
``s3`` profile. It talks to Garage's admin API v2 with the standard library
only, because the Garage image has no shell.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_NOT_FOUND = 404


class _GarageSwitch(BaseSettings):
    """Only the admin token, to decide whether the Garage step runs at all."""

    model_config = SettingsConfigDict(env_prefix="GARAGE_", extra="ignore", hide_input_in_errors=True)

    admin_token: SecretStr = SecretStr("")


def garage_requested() -> bool:
    """Return whether the Garage step should run, i.e. whether ``GARAGE_ADMIN_TOKEN`` is set.

    Returns:
        True when a non-empty admin token is configured.
    """
    return bool(_GarageSwitch().admin_token.get_secret_value().strip())


class GarageSetupSettings(BaseSettings):
    """Garage admin connection, the DuckLake bucket, and the two keys to provision."""

    model_config = SettingsConfigDict(env_prefix="GARAGE_", extra="ignore", hide_input_in_errors=True)

    admin_url: str = "http://garage:3903"
    admin_token: SecretStr = Field(min_length=1)
    bucket: str = Field(min_length=1)
    writer_key_id: str = Field(pattern=r"^GK[0-9a-f]{24}$")
    writer_secret: SecretStr = Field(min_length=64, max_length=64)
    reader_key_id: str = Field(pattern=r"^GK[0-9a-f]{24}$")
    reader_secret: SecretStr = Field(min_length=64, max_length=64)
    # A single-node layout only needs some capacity; Garage uses it as a weight.
    capacity_bytes: int = 1_000_000_000


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
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 -- see above
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code == _NOT_FOUND:
                return None
            detail = exc.read().decode(errors="replace")
            logger.error("Garage %s failed with HTTP %d: %s", endpoint, exc.code, detail)  # noqa: TRY400 -- re-raised below
            raise


def _wait_for_status(admin: GarageAdmin, max_attempts: int = 30, delay_seconds: float = 2.0) -> dict[str, Any]:
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


def setup_garage(settings: GarageSetupSettings | None = None) -> None:
    """Make Garage ready for DuckLake: layout, bucket, and the writer and reader key.

    Args:
        settings: Loaded from ``GARAGE_*`` environment variables if not provided.
    """
    if settings is None:
        settings = GarageSetupSettings()  # ty: ignore[missing-argument]
    admin = GarageAdmin(settings.admin_url, settings.admin_token)
    _ensure_layout(admin, _wait_for_status(admin), settings.capacity_bytes)
    bucket_id = _ensure_bucket(admin, settings.bucket)
    _ensure_key(admin, "ducklake-writer", settings.writer_key_id, settings.writer_secret)
    _ensure_key(admin, "ducklake-reader", settings.reader_key_id, settings.reader_secret)
    _grant(admin, bucket_id, settings.writer_key_id, write=True)
    _grant(admin, bucket_id, settings.reader_key_id, write=False)
    logger.info(
        "Garage ready: bucket '%s', writer %s, reader %s.",
        settings.bucket,
        settings.writer_key_id,
        settings.reader_key_id,
    )
