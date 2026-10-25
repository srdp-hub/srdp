"""Tests for the DuckLake data volume check."""

import os
from pathlib import Path

import pytest

from srdp.setup.volume import VolumeNotWritableError, check_data_dir


def test_missing_data_dir_is_skipped(tmp_path: Path) -> None:
    check_data_dir(None)
    check_data_dir(tmp_path / "absent")


def test_writable_data_dir_passes(tmp_path: Path) -> None:
    check_data_dir(tmp_path)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_foreign_directory_names_the_fix(tmp_path: Path) -> None:
    table = tmp_path / "main" / "orders"
    table.mkdir(parents=True)
    table.chmod(0o500)

    with pytest.raises(VolumeNotWritableError, match=r"orders is not writable.*chown busybox -R"):
        check_data_dir(tmp_path)
