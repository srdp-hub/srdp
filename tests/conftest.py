import os

import pytest


@pytest.fixture(autouse=True)
def _clean_ducklake_env(monkeypatch, tmp_path):
    """Keep the developer's own DUCKLAKE_* variables and any .env file out of the settings under test."""
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("DUCKLAKE_"):
            monkeypatch.delenv(name)
