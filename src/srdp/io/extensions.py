"""Install the DuckDB extensions SRDP needs into one directory, for image builds.

Run as ``python -m srdp.io.extensions <directory> [extra extension ...]``. The images set
``DUCKLAKE_EXTENSION_DIRECTORY`` to that directory, so no container downloads code at start.
"""

import logging
import re
import sys

import duckdb

logger = logging.getLogger(__name__)

EXTENSIONS = ("ducklake", "httpfs", "postgres")
_NAME = re.compile(r"[a-z][a-z0-9_]*")


def install_extensions(directory: str, extra: tuple[str, ...] = ()) -> None:
    """Download the SRDP extensions, plus any ``extra`` ones, into ``directory``.

    Args:
        directory: Target directory. DuckDB adds the version and platform below it.
        extra: Extension names on top of ``EXTENSIONS``, such as ``ui`` for the DuckDB UI image.
    """
    names = (*EXTENSIONS, *extra)
    for name in names:
        if not _NAME.fullmatch(name):
            msg = f"invalid DuckDB extension name: {name!r}"
            raise ValueError(msg)
    conn = duckdb.connect()
    conn.execute("SET extension_directory = ?", [directory])
    for name in names:
        conn.execute(f"INSTALL {name}")
        logger.info("Installed DuckDB extension %s into %s.", name, directory)


def main(argv: list[str]) -> None:
    """Read the directory and the extra extension names from the command line and install them."""
    if not argv:
        msg = "usage: python -m srdp.io.extensions <directory> [extra extension ...]"
        raise SystemExit(msg)
    install_extensions(argv[0], tuple(argv[1:]))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main(sys.argv[1:])
