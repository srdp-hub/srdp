"""Warn about docs pages that are missing from the menu in `zensical.toml`.

The script only warns and always exits with 0, so it never blocks a commit.
"""

import logging
import tomllib
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"


def nav_paths(entries: list[Any]) -> set[str]:
    """Collect the page paths of a nav list, including nested sections.

    Args:
        entries: The ``nav`` list from ``zensical.toml``.

    Returns:
        The set of page paths, relative to the docs folder.
    """
    paths: set[str] = set()
    for entry in entries:
        if isinstance(entry, str):
            paths.add(entry)
            continue
        for value in entry.values():
            paths |= nav_paths(value) if isinstance(value, list) else {value}
    return paths


def main() -> int:
    """Log a warning for every docs page that is not in the menu.

    Returns:
        Always 0, because the check is a warning.
    """
    logging.basicConfig(format="%(message)s")
    config = tomllib.loads((REPO_ROOT / "zensical.toml").read_text())
    listed = nav_paths(config["project"]["nav"])
    pages = {path.relative_to(DOCS_DIR).as_posix() for path in DOCS_DIR.rglob("*.md")}
    for page in sorted(pages - listed):
        logger.warning("Warning: docs/%s is not in the nav of zensical.toml.", page)
    for page in sorted(listed - pages):
        logger.warning("Warning: the nav of zensical.toml lists docs/%s, which does not exist.", page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
