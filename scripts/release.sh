#!/usr/bin/env bash
# Release script for SRDP.
# Usage: ./scripts/release.sh <version>
# Example: ./scripts/release.sh 0.3.0
#
# GitHub Flow: releases tag a commit on main directly, there is no separate
# release branch. This script bumps the version, renames the CHANGELOG.md
# [Unreleased] section to the new version, runs CI, commits, and tags. It
# does not build or publish anything, SRDP isn't published to PyPI yet.
# Pushing the tag triggers .github/workflows/release.yml, which drafts a
# GitHub Release for someone to review and edit before publishing, it never
# publishes on its own.
#
# CHANGELOG.md entries are written by hand under [Unreleased] as changes
# land, not generated. See the tracking issue for switching to git-cliff
# once commit/PR message conventions are established.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

error() {
    echo -e "${RED}ERROR: $1${NC}" >&2
    exit 1
}

info() {
    echo -e "${GREEN}INFO: $1${NC}"
}

warn() {
    echo -e "${YELLOW}WARNING: $1${NC}"
}

if [ $# -eq 0 ]; then
    error "Version number required. Usage: ./scripts/release.sh 0.3.0"
fi

VERSION="$1"

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    error "Invalid version format. Use semantic versioning: X.Y.Z (e.g., 0.3.0)"
fi

if [ -n "$(git status --porcelain)" ]; then
    error "Working directory is not clean. Commit or stash changes first."
fi

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [ "$CURRENT_BRANCH" != "main" ]; then
    error "Releases tag main directly (GitHub Flow). You're on '$CURRENT_BRANCH', switch to main first."
fi

if git rev-parse "v$VERSION" >/dev/null 2>&1; then
    error "Tag v$VERSION already exists."
fi

PREVIOUS_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")

info "Starting release process for version $VERSION"

info "Updating version in pyproject.toml..."
sed -i.bak "s/^version = \".*\"/version = \"$VERSION\"/" pyproject.toml
rm pyproject.toml.bak

if ! grep -q "^## \[Unreleased\]" CHANGELOG.md; then
    error "CHANGELOG.md has no [Unreleased] section to release."
fi

UNRELEASED_BODY=$(awk '/^## \[Unreleased\]/{flag=1; next} /^## /{flag=0} flag' CHANGELOG.md)
if [ -z "$(echo "$UNRELEASED_BODY" | tr -d '[:space:]')" ]; then
    warn "CHANGELOG.md's [Unreleased] section is empty. Add entries before releasing, or continue if this is intentional."
fi

info "Renaming CHANGELOG.md's [Unreleased] section to $VERSION..."
RELEASE_DATE=$(date +%Y-%m-%d)
sed -i.bak "s/^## \[Unreleased\]/## [Unreleased]\n\n## [$VERSION] - $RELEASE_DATE/" CHANGELOG.md
rm CHANGELOG.md.bak
if [ -n "$PREVIOUS_TAG" ]; then
    sed -i.bak "s#^\[Unreleased\]: .*#[Unreleased]: https://github.com/srdp-hub/srdp/compare/v$VERSION...HEAD\n[$VERSION]: https://github.com/srdp-hub/srdp/compare/$PREVIOUS_TAG...v$VERSION#" CHANGELOG.md
    rm CHANGELOG.md.bak
fi

info "Running just ci..."
if ! just ci; then
    git checkout -- pyproject.toml CHANGELOG.md
    error "just ci failed. Fix it before releasing, version bump and changelog reverted."
fi

info "Committing version bump..."
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to $VERSION"

info "Creating git tag v$VERSION..."
git tag "v$VERSION"

echo ""
info "Release $VERSION prepared."
echo ""
echo "Next steps:"
echo "  1. Push the commit: git push origin main"
echo "  2. Push the tag:    git push origin v$VERSION"
echo "  3. This triggers .github/workflows/release.yml, which drafts a GitHub Release."
echo "  4. Review and edit the draft, then publish it: https://github.com/srdp-hub/srdp/releases"
