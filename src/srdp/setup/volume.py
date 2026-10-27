"""Check that the DuckLake data volume is writable by the user the images run as."""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_FIX = "docker run --rm --user 0 -v srdp-ducklake-data:/data --entrypoint chown busybox -R {uid}:{gid} /data"


class VolumeNotWritableError(Exception):
    """Raised when the data directory belongs to another user."""


def _unwritable_dir(data_dir: Path) -> Path | None:
    """Return the first directory under data_dir that this user cannot read or write."""
    unreadable: list[OSError] = []
    for dirpath, _, _ in os.walk(data_dir, onerror=unreadable.append):
        if not os.access(dirpath, os.W_OK | os.X_OK):
            return Path(dirpath)
    return Path(unreadable[0].filename) if unreadable else None


def check_data_dir(data_dir: Path | None) -> None:
    """Fail before any change when the volume holds directories of another user.

    A volume that an older release filled is owned by root. The images run as an
    unprivileged user now, so the writer could not materialize a table into it.
    Docker fixes the ownership of an empty volume by itself, so only a volume with
    data needs this check.

    Args:
        data_dir: The mounted DuckLake data directory, or None when setup has none.

    Raises:
        VolumeNotWritableError: If a directory in it exists that this user cannot write to.
    """
    if data_dir is None or not data_dir.is_dir():
        return
    blocked = _unwritable_dir(data_dir)
    if blocked is None:
        logger.info("The data directory %s is writable.", data_dir)
        return
    fix = _FIX.format(uid=os.geteuid(), gid=os.getegid())
    msg = (
        f"{blocked} is not writable by uid {os.geteuid()}. An earlier release created it as root. "
        f"Run this command once and deploy again. It keeps all data. {fix}"
    )
    raise VolumeNotWritableError(msg)
