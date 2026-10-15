# Maintaining the documentation

The documentation uses MkDocs with the Material theme. Markdown pages and assets live in `docs/`; `mkdocs.yml` defines navigation, styling, search, and link validation. The documentation build is separate from the application environment and does not need Docker, a running cluster, or credentials.

## Preview locally

From the repository root on Windows:

```powershell
uv venv .runtime/docs-venv --python 3.12
uv pip sync docs/requirements.txt --python .runtime/docs-venv/Scripts/python.exe
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

Use fictional examples. Keep passwords, API keys, invitation tokens, customer information, private assignment text, and raw recordings out of documentation. The existing dashboard screenshot is excluded from the website because it identifies the local operator.

## Dependency updates

`docs/requirements.in` pins the direct dependencies. `docs/requirements.txt` locks the full dependency tree with hashes for Windows and Linux. Update deliberately, then regenerate the lock:

```powershell
uv pip compile docs/requirements.in --python-version 3.12 --universal --output-file docs/requirements.txt --generate-hashes
uv pip sync docs/requirements.txt --python .runtime/docs-venv/Scripts/python.exe
.runtime/docs-venv/Scripts/python.exe -m mkdocs build --strict
```

The website uses system fonts and the project's original SVG mark. It does not publish the Switzer font binary. Search runs in the browser; the site does not need an application server or analytics service. Mermaid loads its renderer when the architecture page is opened.

## Read the Docs configuration

`.readthedocs.yaml` selects Ubuntu 24.04, Python 3.12, the locked requirements, and a strict MkDocs build. On Read the Docs, `READTHEDOCS_CANONICAL_URL` supplies the canonical URL. No deployment URL is hardcoded or assumed to be live.

Read the Docs Community accepts public repositories for open source documentation. Private repositories require its paid Business platform. This project's application repository is private. Publication therefore requires a suitable account and an explicitly approved documentation source; the configuration alone does not publish a site or change repository visibility. See the [platform comparison](https://about.readthedocs.com/choosing-a-platform/) and [project import guide](https://docs.readthedocs.com/platform/stable/intro/add-project.html).

If a separate documentation repository is approved, include `docs/`, `mkdocs.yml`, `.readthedocs.yaml`, the chosen documentation license, a `.gitignore` covering `.runtime/`, and a documentation-only CI workflow. Exclude the operator screenshot, generated site, runtime files, assignment file, application source, and application Git history. Keep documentation changes synchronized with the application revision they describe.

After importing that source, verify a successful Read the Docs build and its actual website URL. Use that verified URL for the application README. Configure the repository integration so documentation commits trigger new builds, then verify a webhook delivery and a rebuilt page. Do not advertise an unverified or guessed address.
