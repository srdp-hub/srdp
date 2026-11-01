import pytest

from srdp.io import extensions


def test_extra_extensions_follow_the_defaults(monkeypatch):
    """The command line directory and extra names reach the installer in order."""
    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(extensions, "install_extensions", lambda directory, extra=(): calls.append((directory, extra)))

    extensions.main(["/opt/duckdb/extensions", "ui"])

    assert calls == [("/opt/duckdb/extensions", ("ui",))]


def test_a_missing_directory_is_refused():
    """Running the module without a directory exits with a usage message."""
    with pytest.raises(SystemExit, match="usage"):
        extensions.main([])


def test_an_invalid_extension_name_is_refused(tmp_path):
    """A name with SQL characters stops the install before DuckDB runs anything."""
    with pytest.raises(ValueError, match="invalid DuckDB extension name"):
        extensions.install_extensions(str(tmp_path), ("spatial; DROP TABLE x",))
