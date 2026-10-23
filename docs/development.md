# Development and testing

Use Python 3.12 with uv for the backend and Node.js 24 for the dashboard. Run commands from the repository root unless a different directory is shown. The [installation guide](getting-started.md) covers the full local Kubernetes deployment.

## Source map

| Directory | Responsibility |
| --- | --- |
| `backend/` | Gateway, sessions, permissions, registry, chat and migrations |
| `upstreams/` | WooCommerce adapter, policy service and shipping service |
| `dashboard/` | React and TypeScript console |
| `docker/wordpress/` | Store image, theme, product artwork and initialization |
| `deploy/` | Helm resources and local/production values |
| `scripts/` | Cluster, release, backup and verification commands |
| `tests/` | Unit, protocol, browser and deployed acceptance checks |
| `examples/` | Independent MCP reference agent |
| `docs/` | Documentation pages, assets and dependency lockfiles |

Read the [architecture](system-design.md) before changing cross-service behavior. The [API reference](CONTRACT.md) describes request schemas and configuration. Generated `.venv`, `.tools`, `.runtime`, build outputs and private `.secrets` are local support directories, not application source.

## Install and check

```powershell
uv sync --frozen
uv run pytest -q
uv run ruff check backend upstreams tests scripts examples
Set-Location dashboard
npm ci
npm run build
Set-Location ..
.tools/helm.exe lint deploy/chart -f deploy/values-local.yaml
.tools/helm.exe lint deploy/chart -f deploy/values-prod.yaml
```

Bootstrap or `scripts/install-tools.ps1` installs Helm under `.tools`. The frontend build type-checks before bundling. Its font preparation step downloads Switzer on a fresh installation; [asset attribution](brand-notes.md) describes its source and license.

PostgreSQL concurrency and activity-aggregation tests need `TEST_POSTGRES_URL` pointing to a disposable PostgreSQL database. They are skipped when it is absent. CI supplies its own database. Never point these fixtures at application data. Linux-specific schema memory checks run on Linux; Windows still exercises input and deadline guards.

## Work on the dashboard

With the local deployment running:

```powershell
Set-Location dashboard
npm run dev
```

Vite proxies API requests to `http://harbor.localhost:8080`. Set `VITE_PROXY_TARGET` before starting Vite to use a different backend. The development server reloads frontend changes; the Kubernetes dashboard still serves its previously built image.

To update the local deployment, return to the repository root and run:

```powershell
pwsh -File scripts/build-images.ps1
pwsh -File scripts/deploy.ps1
```

Use the complete deployment to verify changes across services. See [operations](operations.md) before changing stored data, database versions or release configuration.

## Browser checks without live mutations

These console harnesses intercept API responses in the browser. They can target a frontend preview or a deployed dashboard, and do not send application mutations or provider requests:

```powershell
uv run playwright install chromium
$env:HARBOR_URL = 'http://harbor.localhost:8080'
uv run python -m tests.browser_accessibility
uv run python -m tests.browser_observability
uv run python -m tests.browser_console
uv run python -m tests.browser_auth_resilience
uv run python -m tests.browser_chat_resilience
uv run python -m tests.browser_management_resilience
uv run python -m tests.browser_activity_resilience
Remove-Item Env:HARBOR_URL
```

They cover keyboard access, responsive layouts, Activity metrics and filters, formatted replies, unsafe Markdown, reading position, approval focus, unsaved permission edits and member controls. The resilience checks exercise expired sessions, account changes during pending requests, provider cooldowns, malformed replies, uncertain mutations and concurrent connection edits. CI runs all seven harnesses against a production frontend build. Browser fixtures cannot establish that a deployed backend or provider works.

## Checks against a running application

Use a test installation with synthetic data and no other active clients. These harnesses use private credentials in `.secrets`; acceptance checks create users and keys, change registry state and call real services.

```powershell
uv run python -m tests.acceptance
uv run python -m tests.browser_ui
uv run python -m tests.browser_invitation
uv run python -m tests.browser_visual
```

Acceptance covers all three MCP upstreams, hidden write tools, disabled connections, SSRF protection, revoked keys and audit isolation. UI regressions combine real sign-in with browser fixtures for failure and race conditions. The invitation check accepts real invitations and changes its browser session. Visual checks inspect widths from 320 to 1440 px. Reports and screenshots stay under `.runtime`; temporary API keys are revoked during cleanup.

### Storefront

The storefront harness needs the starter bottle and notebook products to be published:

```powershell
uv run python -m tests.browser_store
```

It checks keyboard add-to-cart, cart counts, quantity changes, remove and undo, empty carts, checkout validation and shipping totals in isolated shopping sessions. It needs no administrator login or Gemini key. By default it places no orders and empties its carts before closing.

Add `--checkout` only on a test deployment to place a synthetic cash-on-delivery order and check the receipt. An uncertain checkout is recorded and never automatically resubmitted; inspect the store before rerunning it. The resulting order remains in the store.

### Live chat and checkout

These checks use the configured Gemini key and consume provider quota. The browser acceptance flow also submits a test checkout:

```powershell
uv run python -m tests.acceptance --chat
uv run python -m tests.browser_acceptance
```

`tests.browser_acceptance --audit-only` checks only Activity. It does not verify checkout or chat.

### Five complete order journeys

`tests.live_order_journey` places five synthetic cash-on-delivery orders, then retrieves each through administrator and member chat and MCP Inspector. It checks order amounts, quantities, shipping, write approvals and cancellation, hidden member write tools, direct-call denial, and Activity attribution. It also exercises desktop and narrow layouts.

Install the [pinned MCP Inspector](#external-client-verification) first. The selected deployment needs an active member's `demo-member.json` beside `development.json`, as created by `tests.acceptance`, and these published starter products: `HARBOR-NOTEBOOK-01`, `HARBOR-BOTTLE-01` and `HARBOR-TOTE-01`. Keep their standard prices and 0.5 kg weight. For a hosted test installation, set the [deployment URLs, credentials and evidence directory](#testing-a-public-deployment) before running:

```powershell
uv run python -m tests.live_order_journey
```

This uses the deployment's configured Gemini key, consumes provider quota and honors `Retry-After` within a bounded retry budget. It cancels its own test orders and revokes its temporary keys; inspect the private report for cleanup failures. Cancelled orders remain in the store's history.

To continue an interrupted run, pass the report path printed by the command:

```powershell
uv run python -m tests.live_order_journey --resume '.runtime/order-journey/<run>/report.json'
```

Resume reuses a recorded successful checkout without placing that order again. It refuses to resubmit an uncertain checkout. Reconcile that attempt in the store before continuing; do not start another run to bypass the guard.

### Testing a public deployment

Set both application URLs and use that deployment's credentials. Copy its `development.json` into a protected, Git-excluded directory. Do not substitute local credentials or include the file in reports.

```powershell
$env:HARBOR_URL = 'https://console.example.com'
$env:HARBOR_STORE_URL = 'https://store.example.com'
$env:HARBOR_TEST_CREDENTIALS = (Resolve-Path '.secrets/staging/development.json').Path
$env:HARBOR_TEST_OUTPUT_DIR = Join-Path (Get-Location) ('.runtime/staging-' + (Get-Date -Format yyyyMMdd-HHmmss))
uv run python -m tests.acceptance
uv run python -m tests.browser_invitation
uv run python -m tests.browser_visual
```

Replace the example hosts with the intended test deployment. Run suites sequentially, use synthetic data and inspect cleanup results. Failed reports are useful for diagnosis; retain them alongside successful reruns.

These variables apply to deployed Python harnesses. Operational recovery and failure scripts have their own cluster selection; review it before using them. Clear the four variables when returning to local testing:

```powershell
Remove-Item Env:HARBOR_URL, Env:HARBOR_STORE_URL, Env:HARBOR_TEST_CREDENTIALS, Env:HARBOR_TEST_OUTPUT_DIR
```

## External client verification

For ordinary client configuration, use [external MCP clients](external-clients.md). To test the integration harness, first install its pinned Inspector in the ignored tools directory:

```powershell
npm.cmd install --prefix .tools/external-client --no-audit --no-fund @modelcontextprotocol/inspector@2.11.0
uv run python -m tests.external_agent --prepare-only
uv run python -m tests.acceptance
uv run python -m tests.external_agent --inspector-only
```

`--prepare-only` checks the installed Inspector without API calls. Acceptance creates a test member and saves its credentials in `.secrets/demo-member.json`, or alongside the selected deployment credentials. `--inspector-only` checks tool discovery and an order read without calling a model. Run these against an idle test installation.

For the natural-language workflow, save a Gemini key in `.secrets/gemini-api-key.txt` for local testing. If `HARBOR_TEST_CREDENTIALS` selects another deployment's credential file, save `gemini-api-key.txt` beside that file instead, such as `.secrets/staging/gemini-api-key.txt`. Then run:

```powershell
uv run python -m tests.external_agent
```

This launches `examples/ask_gateway.py`, requires successful calls to at least two upstreams under the member's identity, and revokes the temporary key afterward. Add `--order-id ID` to include a specific existing order in the shipping check. The report is saved privately under `.runtime`; an answer that merely claims to have called tools does not pass.

## Documentation

MkDocs builds a static site from `docs/`. Install its locked dependencies in a separate environment, then preview on loopback:

```powershell
uv venv .runtime/docs-venv --python 3.12
uv pip sync docs/requirements.txt --require-hashes --python .runtime/docs-venv/Scripts/python.exe
.runtime/docs-venv/Scripts/python.exe -m mkdocs serve --dev-addr 127.0.0.1:8765
```

Open `http://127.0.0.1:8765`. On Linux or macOS, use `.runtime/docs-venv/bin/python`. For publication, run the strict build:

```powershell
.runtime/docs-venv/Scripts/python.exe scripts/build-docs.py
```

The helper validates approved inputs, relative links, heading anchors and publication exclusions. New pages need entries in `mkdocs.yml` and `PUBLIC_DOCS` in `scripts/build-docs.py`. Use relative `.md` links and check changed pages on both desktop and narrow screens. Files under `docs/` can become public even when absent from navigation, so review assets as carefully as prose.

Publish only `.runtime/docs-site`, never the repository root, `.runtime` or the raw source directory. The documentation build needs no application credentials or running cluster. `DOCS_SITE_URL` sets canonical links and should match the public documentation address. The site's indexing directives do not provide access control.

## Continuous integration

CI checks locked dependencies, Python tests, disposable PostgreSQL cases, credential and recovery guards, the dashboard build, and the fixture-based browser suites. Separate jobs validate the documentation, lint/render Helm and build all three application images. Container checks cover authentication memory limits and preservation of store data during theme migration.

CI does not provision a cluster or exercise a live Gemini request. Run the relevant deployed checks for changes to networking, credentials, migrations or cross-service behavior. Keep credentials, customer data and raw runtime reports outside commits.
