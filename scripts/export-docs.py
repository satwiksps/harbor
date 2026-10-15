"""Export reviewed documentation into an isolated publication checkout.

Run with Python 3.12: python scripts/export-docs.py
An optional --output directory must stay below this repository's .runtime directory.
The export preserves .git and ignored .runtime output, and never publishes or runs Git writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).absolute().parents[1]
SOURCE_FILES = (
    ".readthedocs.yaml",
    "mkdocs.yml",
    "docs/CONTRACT.md",
    "docs/audit.md",
    "docs/brand-notes.md",
    "docs/demo.md",
    "docs/development.md",
    "docs/documentation.md",
    "docs/external-clients.md",
    "docs/getting-started.md",
    "docs/index.md",
    "docs/operations.md",
    "docs/system-design.md",
    "docs/threat-model.md",
    "docs/user-guide.md",
    "docs/verification.md",
    "docs/requirements.in",
    "docs/requirements.txt",
    "docs/robots.txt",
    "docs/assets/external-agent-evidence.json",
    "docs/assets/mark.svg",
    "docs/stylesheets/extra.css",
    "docs/theme/main.html",
)
BLOCKED_CONTENT = re.compile(
    r"urumi|\b(?:recruit(?:ment|ing|ers?)?|submissions?|internships?|assignments?)\b",
    re.IGNORECASE,
)
README = """# Commerce Console documentation

[Read the documentation](https://commerce-console-docs.readthedocs.io/en/latest/).

Setup, user guides, architecture, operations, and verification records for Commerce Console.
This repository contains documentation only. Application source and application Git history
are maintained separately.

Build with Python 3.12 and uv:

```sh
uv venv .runtime/docs-venv --python 3.12
uv pip sync docs/requirements.txt --require-hashes --python .runtime/docs-venv/bin/python
.runtime/docs-venv/bin/python -m mkdocs build --strict
```

On Windows, use `.runtime/docs-venv/Scripts/python.exe` instead of
`.runtime/docs-venv/bin/python`. Generated pages are written to `.runtime/docs-site`.

See [Maintaining the documentation](https://commerce-console-docs.readthedocs.io/en/latest/documentation/)
for maintenance and Read the Docs setup.
The documentation and original documentation assets are available under the MIT License.
This license does not cover the separately maintained application source.
"""
LICENSE = """MIT License

Copyright (c) 2026 Satwik Sai Prakash Sahoo

This license applies to the documentation and original documentation assets in
this repository. It does not apply to the separately maintained application
source code. Third-party dependencies retain their own licenses.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
DOCS_CI = """name: Documentation checks
on:
  push:
  pull_request:
permissions:
  contents: read
jobs:
  documentation:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v5
      - uses: astral-sh/setup-uv@v6
        with:
          version: 0.12.23
      - run: uv venv .runtime/docs-venv --python 3.12
      - run: uv pip sync docs/requirements.txt --require-hashes --python .runtime/docs-venv/bin/python
      - run: .runtime/docs-venv/bin/python -m mkdocs build --strict
"""


def require_plain_path(path: Path) -> None:
    """Do not follow symlinks or Windows junctions, including parent components."""
    for component in (path, *path.parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError(f"Linked path is not allowed: {component}")


def read_sources() -> dict[str, bytes]:
    files = {}
    for name in SOURCE_FILES:
        path = ROOT / name
        require_plain_path(path)
        data = path.read_bytes()
        if BLOCKED_CONTENT.search(data.decode("utf-8")):
            raise ValueError(f"Publication content review required: {name}")
        files[name] = data
    files.update(
        {
            "README.md": README.encode(),
            "LICENSE": LICENSE.encode(),
            ".gitignore": b".runtime/\n",
            ".github/workflows/docs.yaml": DOCS_CI.encode(),
        }
    )
    return files


def check_destination(output: Path, allowed: set[str]) -> Path:
    require_plain_path(output)
    runtime = (ROOT / ".runtime").resolve()
    output = output.resolve()
    if output == runtime or not output.is_relative_to(runtime):
        raise ValueError("Output must be a directory below this repository's .runtime directory")
    if output.exists() and not output.is_dir():
        raise ValueError("Output must be a directory")
    for directory, subdirs, filenames in os.walk(output):
        parent = Path(directory)
        for name in [*subdirs, *filenames]:
            path = parent / name
            require_plain_path(path)
            relative = path.relative_to(output).as_posix()
            if relative == ".git":
                if name in subdirs:
                    subdirs.remove(name)
                continue
            if relative == ".runtime" and name in subdirs:
                # Preserve ignored builds and environments without inspecting or following their links.
                subdirs.remove(name)
                continue
            if path.is_file() and relative not in allowed:
                raise ValueError(f"Unrelated destination file, export refused: {relative}")
    return output


def export(output: Path) -> Path:
    files = read_sources()
    output = check_destination(output, set(files))
    manifest_path = output.with_name(f"{output.name}-manifest.json")
    require_plain_path(manifest_path)
    source_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    source_dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    )
    for name, data in files.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    entries = [
        {"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in sorted(files.items())
    ]
    manifest = {
        "status": "local export only; public hosting is not performed by this script",
        "generatedAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSha": source_sha,
        "sourceWorkingTreeDirty": source_dirty,
        "sourceNote": "Per-file hashes identify the export, including any uncommitted changes.",
        "publicationDirectory": str(output),
        "license": "MIT for documentation and original documentation assets only",
        "fileCount": len(entries),
        "totalBytes": sum(item["bytes"] for item in entries),
        "files": entries,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/docs-publication")
    args = parser.parse_args()
    try:
        manifest = export(args.output.absolute())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Export refused: {exc}", file=sys.stderr)
        return 1
    print(f"Exported {len(SOURCE_FILES) + 4} documentation files. Manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
