"""Check that one Python and one Dagster version run everywhere, from the pyproject and lockfile."""

import re
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKIPPED_DIRS = {".venv", "node_modules", ".git", ".claude"}
PYTHON_IMAGE = re.compile(r"^FROM\s+\S*python:?(\d+\.\d+)", re.IGNORECASE | re.MULTILINE)


def dockerfiles() -> list[Path]:
    """Return every Dockerfile in the repo, platform and project ones alike."""
    return [
        path
        for pattern in ("**/Dockerfile", "**/*.Dockerfile")
        for path in REPO_ROOT.glob(pattern)
        if not SKIPPED_DIRS & set(path.relative_to(REPO_ROOT).parts)
    ]


def locked_version(package: str) -> str:
    """Return the version `uv.lock` pins for a package."""
    lock = tomllib.loads((REPO_ROOT / "uv.lock").read_text())
    return next(p["version"] for p in lock["package"] if p["name"] == package)


def test_every_dockerfile_python_is_the_pyproject_minor() -> None:
    """Build and runtime stages, and every project image, use the Python the pyproject allows."""
    requires = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    lower, upper = (tuple(map(int, re.search(r"(\d+)\.(\d+)", part).groups())) for part in requires.split(","))
    assert upper == (lower[0], lower[1] + 1), f"requires-python {requires} should allow exactly one minor"
    wanted = f"{lower[0]}.{lower[1]}"
    found = {str(path.relative_to(REPO_ROOT)): set(PYTHON_IMAGE.findall(path.read_text())) for path in dockerfiles()}
    assert any(found.values())
    wrong = {path: minors for path, minors in found.items() if minors and minors != {wanted}}
    assert not wrong, wrong


def test_every_python_image_runs_the_same_python_build() -> None:
    """Build and runtime stages of every image share one python image reference and one uv, so no container drifts a patch version."""
    texts = [path.read_text() for path in dockerfiles()]
    pythons = {ref for text in texts for ref in re.findall(r"^FROM\s+(python:\S+)", text, re.MULTILINE)}
    uvs = {ref for text in texts for ref in re.findall(r"^COPY --from=(ghcr\.io/astral-sh/uv:\S+)", text, re.MULTILINE)}
    assert len(pythons) == 1, pythons
    assert len(uvs) == 1, uvs
    assert all("@sha256:" in ref for ref in pythons | uvs)


def test_python_version_file_matches_the_pyproject_minor() -> None:
    """`uv` and CI pick the same interpreter the images run."""
    requires = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]["requires-python"]
    lower = re.search(r"(\d+\.\d+)", requires)[1]
    assert (REPO_ROOT / ".python-version").read_text().strip() == lower


def test_dagster_chart_matches_the_locked_dagster() -> None:
    """The chart dependency is the release line `uv.lock` resolves, so the UI and the code location agree."""
    chart = yaml.safe_load((REPO_ROOT / "deploy/kubernetes/srdp-chart/Chart.yaml").read_text())
    version = next(d["version"] for d in chart["dependencies"] if d["name"] == "dagster")
    assert version == locked_version("dagster")
