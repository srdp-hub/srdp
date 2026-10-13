#!/usr/bin/env bash
# Export one docs page to a GitHub-styled PDF, with Mermaid diagrams as images.
# Usage: ./scripts/docs-pdf.sh <docs page.md>
# Example: ./scripts/docs-pdf.sh docs/11-single-pager-srdp.md
#
# Strips the Zensical front matter, points relative links to other docs pages
# at the published docs site (site_url in zensical.toml),
# renders each Mermaid block to a PNG with mermaid-cli and turns the result
# into an A4 PDF with md-to-pdf and GitHub's markdown stylesheet.
# Needs Node.js (npx). The tools are pinned and fetched on first use.
#
# Writes to dist/pdf/<page name>.pdf, plus the diagram PNGs next to it.

set -euo pipefail

MERMAID_CLI="@mermaid-js/mermaid-cli@12.0.0"
MD_TO_PDF="md-to-pdf@5.2.5"
GITHUB_CSS="https://cdn.jsdelivr.net/npm/github-markdown-css@5.9.0/github-markdown-light.css"

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
    echo "Usage: $0 <docs page.md>" >&2
    exit 1
fi

page="$1"
name="$(basename "$page" .md)"
script_dir="$(cd "$(dirname "$0")" && pwd)"
out_dir="dist/pdf"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

mkdir -p "$out_dir"

# Drop the front matter block (only when the file starts with ---), then turn
# relative links to .md pages into absolute docs site URLs, so they work in a shared PDF.
awk 'NR == 1 && /^---$/ { fm = 1; next } fm && /^---$/ { fm = 0; next } !fm' "$page" \
    | uv run --no-project python -c '
import posixpath, re, sys, tomllib
site = tomllib.load(open("zensical.toml", "rb"))["project"]["site_url"].rstrip("/")
page_dir = posixpath.dirname(posixpath.relpath(sys.argv[1], "docs"))

def absolute(match):
    path, anchor = match.group(1), match.group(2) or ""
    target = posixpath.normpath(posixpath.join(page_dir, path))[: -len(".md")]
    target = "" if target == "index" else target.removesuffix("/index") + "/"
    return f"]({site}/{target}{anchor})"

sys.stdout.write(re.sub(r"\]\((?!https?://)([^)#\s]+\.md)(#[^)\s]*)?\)", absolute, sys.stdin.read()))
' "$page" > "$work_dir/page.md"

npx -y "$MERMAID_CLI" -i "$work_dir/page.md" -o "$out_dir/$name.md" -e png -s 2 -q

curl -fsSL "$GITHUB_CSS" -o "$work_dir/github.css"

npx -y "$MD_TO_PDF" "$out_dir/$name.md" \
    --stylesheet "$work_dir/github.css" \
    --stylesheet "$script_dir/docs-pdf.css" \
    --body-class markdown-body \
    --pdf-options '{"format":"A4","margin":"15mm","printBackground":true}' \
    < /dev/null  # md-to-pdf waits for stdin to close, even when given a file

rm "$out_dir/$name.md"
echo "Wrote $out_dir/$name.pdf"
