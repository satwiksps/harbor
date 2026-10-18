"""Build only reviewed public documentation from the private application repository."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
PUBLIC_DOCS = {
    "CONTRACT.md",
    "LICENSE",
    "audit.md",
    "brand-notes.md",
    "demo.md",
    "development.md",
    "documentation.md",
    "external-clients.md",
    "getting-started.md",
    "index.md",
    "operations.md",
    "system-design.md",
    "threat-model.md",
    "user-guide.md",
    "verification.md",
    "requirements.in",
    "requirements.txt",
    "robots.txt",
    "assets/external-agent-evidence.json",
    "assets/mark.svg",
    "stylesheets/extra.css",
    "theme/main.html",
    "theme/partials/header.html",
}
BLOCKED_CONTENT = re.compile(
    r"urumi|\b(?:recruit(?:ment|ing|ers?)?|submissions?|internships?|assignments?)\b",
    re.IGNORECASE,
)


def require_plain_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction():
            raise ValueError(f"Linked documentation path is not allowed: {part}")


def validate_sources() -> None:
    docs = ROOT / "docs"
    require_plain_path(docs)
    found = set()
    for directory, subdirs, filenames in os.walk(docs):
        for name in [*subdirs, *filenames]:
            path = Path(directory) / name
            require_plain_path(path)
            if not path.is_file():
                continue
            relative = path.relative_to(docs).as_posix()
            if relative not in PUBLIC_DOCS:
                raise ValueError(f"Documentation publication review required: docs/{relative}")
            if BLOCKED_CONTENT.search(path.read_text(encoding="utf-8")):
                raise ValueError(f"Restricted wording in docs/{relative}")
            found.add(relative)
    missing = PUBLIC_DOCS - found
    if missing:
        raise ValueError(f"Missing documentation sources: {', '.join(sorted(missing))}")
    config = ROOT / "mkdocs.yml"
    require_plain_path(config)
    if BLOCKED_CONTENT.search(config.read_text(encoding="utf-8")):
        raise ValueError("Restricted wording in mkdocs.yml")


def validate_output(output: Path) -> int:
    for relative in ("index.html", "search/search_index.json", "robots.txt", "LICENSE"):
        if not (output / relative).is_file():
            raise ValueError(f"Missing documentation output: {relative}")
    for relative in ("assets/console.png", "requirements.in", "requirements.txt", "theme"):
        if (output / relative).exists():
            raise ValueError(f"Private or build-only source in documentation output: {relative}")
    pages = list(output.rglob("*.html"))
    for page in pages:
        content = page.read_text(encoding="utf-8")
        if '<meta name="robots" content="noindex, nofollow, noarchive">' not in content:
            raise ValueError(f"Missing indexing directive: {page.relative_to(output)}")
    return len(pages)


def main() -> int:
    try:
        validate_sources()
        output = ROOT / ".runtime/docs-site"
        require_plain_path(output)
        subprocess.run(
            [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(output)],
            cwd=ROOT,
            check=True,
        )
        pages = validate_output(output)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Documentation build refused: {exc}", file=sys.stderr)
        return 1
    print(f"Validated {pages} documentation pages. Publish only .runtime/docs-site.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
