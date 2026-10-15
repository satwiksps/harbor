# Maintaining the documentation

The documentation uses MkDocs with the Material theme. Markdown pages and assets live in `docs/`; `mkdocs.yml` defines navigation, styling, search, and link validation. The documentation build is separate from the application environment and does not need Docker, a running cluster, or credentials.

## Preview locally

From the repository root on Windows:

```powershell
uv venv .runtime/docs-venv --python 3.12
uv pip sync docs/requirements.txt --require-hashes --python .runtime/docs-venv/Scripts/python.exe
.runtime/docs-venv/Scripts/python.exe -m mkdocs serve --dev-addr 127.0.0.1:8765
```

Open <http://127.0.0.1:8765>. Edits reload automatically. On macOS or Linux, use `.runtime/docs-venv/bin/python` instead of the Windows interpreter path.

For a static build:

```powershell
.runtime/docs-venv/Scripts/python.exe -m mkdocs build --strict
```

The output is written to ignored `.runtime/docs-site`. Strict mode treats missing pages, broken relative links, and invalid heading anchors as build failures. CI runs the same documentation build on every push and pull request.

## Add or update a page

1. Edit a Markdown page in `docs/` and link related guides using relative `.md` paths. MkDocs converts those paths to website URLs.
2. Add new pages to the `nav` section in `mkdocs.yml`.
3. Put diagrams in fenced `mermaid` blocks. Describe the same relationships in the surrounding text so the page remains useful without JavaScript.
4. Run the strict build, then check the changed page on a desktop and a narrow screen. Check tables, code blocks, search results, and heading links.
5. Keep outcomes and limitations together. Recorded verification is evidence from a particular run, not a guarantee that every deployment has passed it.

Use fictional examples. Keep passwords, API keys, invitation tokens, customer information, private project material, and raw recordings out of documentation. The existing dashboard screenshot is excluded from the website because it identifies the local operator.

## Dependency updates

`docs/requirements.in` pins the direct dependencies. `docs/requirements.txt` locks the full dependency tree with hashes for Windows and Linux. Update deliberately, then regenerate the lock:

```powershell
uv pip compile docs/requirements.in --python-version 3.12 --universal --output-file docs/requirements.txt --generate-hashes
uv pip sync docs/requirements.txt --require-hashes --python .runtime/docs-venv/Scripts/python.exe
.runtime/docs-venv/Scripts/python.exe -m mkdocs build --strict
```

The website uses system fonts and the project's original SVG mark. It does not publish the Switzer font binary. Search runs in the browser; the site does not need an application server or analytics service. Mermaid loads its renderer when the architecture page is opened.

## Hosting and publication

The [documentation website](https://commerce-console-docs.readthedocs.io/en/latest/) is hosted on Read the Docs Community. Its source is the separate [documentation repository](https://github.com/satwiksps/commerce-console-docs). The application repository remains private. The MIT license in the documentation repository covers the documentation and original documentation assets only.

`.readthedocs.yaml` selects Ubuntu 24.04, Python 3.12, the locked requirements, and a strict MkDocs build. `READTHEDOCS_CANONICAL_URL` supplies the canonical URL during hosted builds. The `latest` version follows the documentation repository's `main` branch. GitHub sends signed webhook events to Read the Docs so pushes rebuild the website. GitHub Actions independently checks the strict build.

The source pages are maintained alongside the application. From the private application checkout, publish reviewed changes with the export script:

```powershell
.runtime/docs-venv/Scripts/python.exe -m mkdocs build --strict
uv run python scripts/export-docs.py
git -C .runtime/docs-publication status --short
git -C .runtime/docs-publication diff
```

The exporter copies an explicit file list into `.runtime/docs-publication` and preserves that checkout's independent Git history. It rejects linked paths, unexpected destination files, and restricted project references. A hash manifest stays beside the export in the private `.runtime` directory. New documentation files must be added to the export list as well as the site navigation.

Review the diff before committing and pushing the documentation checkout's `main` branch. Its remote must be `https://github.com/satwiksps/commerce-console-docs.git`. On a new workstation, first clone that repository into `.runtime/docs-publication`, then run the exporter. Never use the application checkout or its Git history as the public documentation source.

After a push, check [GitHub Actions](https://github.com/satwiksps/commerce-console-docs/actions) and [Read the Docs builds](https://app.readthedocs.org/projects/commerce-console-docs/builds/), then open the changed page on the website. A successful local build does not confirm that hosting has updated. If a build does not start, inspect the GitHub webhook's recent deliveries and the project's Read the Docs integration activity.

## Publication boundaries

The export excludes application source, application Git history, credentials, the operator screenshot, generated pages, and private runtime records. Keep examples fictional and review each change for identifying details before publication.

Each page requests `noindex, nofollow, noarchive` through its robots meta tag. `robots.txt` permits crawling so search engines can read that request. These directives reduce indexing by cooperating search engines; they do not restrict access to the public website or GitHub repository.
