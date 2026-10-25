"""Check that every image reference and every GitHub Action is pinned by digest or commit SHA."""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKIPPED_DIRS = {".venv", "node_modules", ".git", ".claude", "site", "charts"}
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}")
# The images SRDP builds are tagged with the release version or dev, never by digest.
OWN_IMAGE = re.compile(r"\$\{SRDP_PLATFORM_REGISTRY|^local-|^ghcr\.io/srdp-hub/")


# Structured chart images that SRDP builds, which follow the release version.
OWN_CHART_IMAGE = re.compile(r"^ghcr\.io/srdp-hub/|/srdp-etl:")


def repo_files(*patterns: str) -> list[Path]:
    """Return the repo files matching the patterns, without vendored directories."""
    return [
        path
        for pattern in patterns
        for path in REPO_ROOT.glob(pattern)
        if not SKIPPED_DIRS & set(path.relative_to(REPO_ROOT).parts)
    ]


def test_every_dockerfile_base_image_has_a_digest() -> None:
    """A tag can move to another image, a digest cannot."""
    unpinned = []
    for path in repo_files("**/Dockerfile", "**/*.Dockerfile"):
        stages = set()
        for line in path.read_text().splitlines():
            match = re.match(r"^FROM\s+(\S+)(?:\s+AS\s+(\S+))?", line, re.IGNORECASE)
            if match:
                if match[1] not in stages and not DIGEST.search(match[1]):
                    unpinned.append(f"{path.relative_to(REPO_ROOT)}: {match[1]}")
                if match[2]:
                    stages.add(match[2])
            copy = re.match(r"^COPY\s+--from=(\S+)", line)
            if copy and "/" in copy[1] and copy[1] not in stages and not DIGEST.search(copy[1]):
                unpinned.append(f"{path.relative_to(REPO_ROOT)}: {copy[1]}")
    assert not unpinned, unpinned


def test_every_compose_image_has_a_digest() -> None:
    """Third-party images in the Compose files are pinned, the images SRDP builds follow the release version."""
    unpinned = []
    for path in repo_files("deploy/docker/docker-compose*.yml"):
        services = yaml.safe_load(path.read_text()).get("services", {})
        for name, service in services.items():
            image = service.get("image")
            if image and not OWN_IMAGE.search(image) and not DIGEST.search(image):
                unpinned.append(f"{path.name} {name}: {image}")
    assert not unpinned, unpinned


def chart_images(node: object, trail: str = "") -> list[tuple[str, str]]:
    """Collect the image references in a chart values tree as (path, reference)."""
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        image = node.get("image")
        if isinstance(image, dict) and "tag" in image and "repository" not in image and "registry" not in image:
            found.append((f"{trail}.image", f"tag={image['tag']}"))
        elif isinstance(image, dict) and "repository" in image and ("tag" in image or "digest" in image):
            digest = f"@{image['digest']}" if "digest" in image else ""
            found.append((f"{trail}.image", f"{image['repository']}:{image.get('tag', '')}{digest}"))
        for key, value in node.items():
            if key == "image" and isinstance(value, str):
                found.append((f"{trail}.image", value))
            else:
                found += chart_images(value, f"{trail}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += chart_images(value, f"{trail}[{index}]")
    return found


def test_every_chart_image_has_a_digest() -> None:
    """Images with a literal reference in a values file carry a digest, except the ones SRDP builds."""
    unpinned = []
    for path in repo_files("deploy/kubernetes/srdp-chart/values*.yaml"):
        for where, reference in chart_images(yaml.safe_load(path.read_text())):
            if (
                "release-tag" in reference
                or reference == "tag=dev"
                or DIGEST.search(reference)
                or OWN_CHART_IMAGE.search(reference)
            ):
                continue
            unpinned.append(f"{path.name} {where}: {reference}")
    assert not unpinned, unpinned


def test_every_workflow_action_is_pinned_to_a_commit() -> None:
    """A tag such as v4 can move to different code, a commit SHA cannot."""
    unpinned = []
    for path in repo_files(".github/workflows/*.yml"):
        for line in path.read_text().splitlines():
            match = re.match(r"^\s*-?\s*uses:\s*(\S+)", line)
            if match and not match[1].startswith("./") and not re.search(r"@[0-9a-f]{40}$", match[1]):
                unpinned.append(f"{path.name}: {match[1]}")
    assert not unpinned, unpinned


def test_every_pre_commit_hook_is_pinned_to_a_commit() -> None:
    """The hooks run on every developer machine and in CI, so a movable tag would change that code."""
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())
    unpinned = [
        repo["repo"]
        for repo in config["repos"]
        if repo["repo"] != "local" and not re.fullmatch(r"[0-9a-f]{40}", repo["rev"])
    ]
    assert not unpinned, unpinned


def test_every_published_image_is_also_built_and_scanned_on_pull_requests() -> None:
    """A release publishes only images that the check job builds and scans first."""
    jobs = yaml.safe_load((REPO_ROOT / ".github/workflows/images.yml").read_text())["jobs"]
    checked = {tuple(sorted(entry.items())) for entry in jobs["check"]["strategy"]["matrix"]["include"]}
    published = {tuple(sorted(entry.items())) for entry in jobs["build"]["strategy"]["matrix"]["include"]}
    assert published, "the build job publishes nothing"
    assert published <= checked, published - checked


def test_chart_images_collects_structured_references() -> None:
    """A structured image with a tag but no digest is found, so the pinning test can fail it."""
    values = {"busybox": {"image": {"repository": "docker.io/busybox", "tag": "latest"}}}
    assert chart_images(values) == [(".busybox.image", "docker.io/busybox:latest")]
