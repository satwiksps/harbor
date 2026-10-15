# Maintaining the documentation

The documentation lives alongside the application in the private main repository. MkDocs and the Material theme turn `docs/` into a static website. `mkdocs.yml` defines navigation, styling, search and link validation. Documentation changes can be reviewed in the same commit as the behavior they describe.

Cloudflare Pages builds the site from this repository's `main` branch and publishes only the generated documentation directory. The documentation environment does not need Docker, a running cluster, application dependencies or application credentials.

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
.runtime/docs-venv/Scripts/python.exe scripts/build-docs.py
```

The helper checks the approved documentation inputs, runs MkDocs in strict mode and checks the output before publication. Missing pages, broken relative links and invalid heading anchors fail the build. Output goes to ignored `.runtime/docs-site`. GitHub Actions runs the same helper on every push and pull request.

## Add or update a page

1. Edit a Markdown page in `docs/` and link related guides using relative `.md` paths. MkDocs converts those paths to website URLs.
2. Add new pages to the `nav` section in `mkdocs.yml` and the approved input list in `scripts/build-docs.py`.
3. Put diagrams in fenced `mermaid` blocks. Describe the same relationships in the surrounding text so the page remains useful without JavaScript.
4. Run the strict build, then check the changed page on a desktop and a narrow screen. Check tables, code blocks, search results, and heading links.
5. Keep outcomes and limitations together. Recorded verification is evidence from a particular run, not a guarantee that every deployment has passed it.

Use fictional examples. Keep passwords, API keys, invitation tokens, customer information, private project material, and raw recordings out of documentation. The existing dashboard screenshot is excluded from the website because it identifies the local operator.

## Dependency updates

`docs/requirements.in` pins the direct dependencies. `docs/requirements.txt` locks the full dependency tree with hashes for Windows and Linux. Update deliberately, then regenerate the lock:

```powershell
uv pip compile docs/requirements.in --python-version 3.12 --universal --output-file docs/requirements.txt --generate-hashes
uv pip sync docs/requirements.txt --require-hashes --python .runtime/docs-venv/Scripts/python.exe
.runtime/docs-venv/Scripts/python.exe scripts/build-docs.py
```

The website uses system fonts and the project's original SVG mark. It does not publish the Switzer font binary. Search runs in the browser; the site does not need an application server or analytics service. Mermaid loads its renderer when the architecture page is opened.

## Hosting and publication

Use a Cloudflare Pages project connected directly to the private application repository through its [GitHub integration](https://developers.cloudflare.com/pages/configuration/git-integration/github-integration/). Limit the GitHub App's repository access to this project. The repository remains private; the generated website is public.

Configure the Pages project with these values:

| Setting | Value |
| --- | --- |
| Production branch | `main` |
| Root directory | Repository root |
| Build command | `pip install --require-hashes -r docs/requirements.txt && python scripts/build-docs.py` |
| Build output directory | `.runtime/docs-site` |
| `PYTHON_VERSION` | `3.12` |
| `SKIP_DEPENDENCY_INSTALL` | `1` |
| `DOCS_SITE_URL` | The verified public site URL, including `https://` and a trailing slash |

The custom build command installs only the locked documentation dependencies. `SKIP_DEPENDENCY_INSTALL` prevents automatic installation of the application's dependency set. See Cloudflare's [build configuration](https://developers.cloudflare.com/pages/configuration/build-configuration/) and [dependency installation setting](https://developers.cloudflare.com/pages/configuration/build-image/#skip-dependency-install).

Set `DOCS_SITE_URL` after confirming the assigned address in the Pages project. MkDocs uses it for canonical URLs. Local builds can leave it unset. Do not substitute an unverified address or a deployment-specific preview URL for the stable site address.

For [build watch paths](https://developers.cloudflare.com/pages/configuration/build-watch-paths/), include `docs/*`, `mkdocs.yml` and `scripts/build-docs.py`. A single trailing `*` includes nested paths in Pages. These paths cover content, assets, dependency locks and build validation, while ordinary application-only changes can skip a website build. Update the list if a new file outside these paths affects the documentation output.

Publish by reviewing and committing the documentation changes in the application checkout, then pushing or merging to `main`. The Pages Git integration triggers deployment. GitHub Actions checks the build independently; a successful CI run alone does not prove that hosting has updated.

After each publication, check the Pages deployment's commit SHA and successful build status, then open the changed page at the production address. Check navigation, search and a narrow-screen layout. If no build starts, inspect the repository connection, production branch and watch paths in Pages. Keep the README's documentation link pointed at the verified production address.

## Publication boundaries

Only `.runtime/docs-site` is the Pages output directory. Never point hosting at the repository root, `.runtime` or `docs` itself. MkDocs reads the approved documentation source and excludes the operator screenshot, dependency files and raw theme templates from direct publication. The build helper verifies those exclusions and the site's indexing directives.

Keep application source, Git metadata, credentials, raw runtime evidence and identifying details out of the generated site. Files placed under `docs/` can become public assets even when they are not linked from navigation, so review new assets as carefully as page text. No application secrets belong in Pages environment variables.

Each page requests `noindex, nofollow, noarchive` through its robots meta tag. `robots.txt` permits crawling so search engines can read that request. These directives reduce indexing by cooperating search engines; they do not restrict access to the public website. The source repository's privacy is a separate setting.

The [documentation license](LICENSE) applies to the documentation and original documentation assets only. It does not license the application source. Third-party fonts and other assets retain their own [licenses and attribution](brand-notes.md).
