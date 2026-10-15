# Development

Use Python 3.12 with uv for the backend and Node.js 24 for frontend development. Run commands from the repository root unless a different directory is shown. The [getting started guide](getting-started.md) covers the full local Kubernetes deployment.

## Source map

| Directory | Responsibility |
| --- | --- |
| `backend/` | Gateway, sessions, permissions, registry, chat and migrations |
| `upstreams/` | WooCommerce adapter, policy server and shipping server |
| `dashboard/` | React and TypeScript dashboard |
| `docker/wordpress/` | Store image, original product artwork and repeatable seed |
| `deploy/` | Helm resources and local/production values |
| `scripts/` | Cluster, release, backup and verification commands |
| `tests/` | Unit, protocol, browser and deployed acceptance checks |
| `examples/` | Independent MCP reference agent |

Read the [system design](system-design.md) before changing cross-service behavior. The [API and environment reference](CONTRACT.md) describes the frontend/backend contract.

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

Helm is installed under `.tools` by bootstrap or `scripts/install-tools.ps1`. The frontend build type-checks before bundling. Its font preparation step downloads Switzer on a fresh installation; [visual provenance](brand-notes.md) documents the font source and licensing.

The PostgreSQL concurrency cases need `TEST_POSTGRES_URL` pointing to a disposable PostgreSQL database. They are skipped when that variable is absent. CI supplies a dedicated PostgreSQL service. Do not point test fixtures at the deployed application's database. Linux-specific schema memory checks run on Linux; Windows checks still exercise input and deadline guards.

## Work on the dashboard

With the local deployment running:

```powershell
Set-Location dashboard
npm run dev
```

Vite proxies API requests to the local console ingress. Use the complete deployment for final verification and demonstrations.

After source changes, rebuild the images and roll out the chart:

```powershell
pwsh -File scripts/build-images.ps1
pwsh -File scripts/deploy.ps1
```

These commands change the running local release. Follow the [operations guide](operations.md) for backups, upgrades, migrations and rollback. A Helm rollback does not rewind database contents.

## Run deployed checks deliberately

The integration harnesses require the running cluster and private credentials in `.secrets`. They are not read-only checks. Acceptance tests create users and keys, alter registry state and exercise real services; the checkout harness creates a product and order. Run them while other clients are idle, with synthetic store data.

```powershell
uv run python -m tests.acceptance
uv run playwright install chromium
uv run python -m tests.browser_ui
uv run python -m tests.browser_invitation
uv run python -m tests.browser_visual
```

The acceptance harness calls all three upstreams over MCP and checks hidden writes, disabled servers, SSRF, revocation and audit isolation. UI regression tests combine real sign-in with browser response fixtures for reproducible failure and race conditions, without calling Gemini. Visual checks inspect the deployed pages at widths from 320 to 1440 px. Runtime evidence stays private under `.runtime`; harness-created API keys are revoked during cleanup.

For accessibility checks with browser-local API fixtures:

```powershell
$env:COMMERCE_CONSOLE_URL = 'http://console.localhost:8080'
uv run python -m tests.browser_accessibility
Remove-Item Env:COMMERCE_CONSOLE_URL
```

This harness can also target a running frontend preview. It does not send application mutations or provider requests.

The following checks use a live Gemini API key and consume quota. The browser acceptance flow also submits a real test checkout:

```powershell
uv run python -m tests.acceptance --chat
uv run python -m tests.browser_acceptance
```

`tests.browser_acceptance --audit-only` writes a separate activity-only report. It does not establish that the complete checkout and chat journey passed.

## External client verification

The [external client guide](external-clients.md) describes MCP Inspector and an independent SDK agent with no backend imports. The [sanitized evidence](assets/external-agent-evidence.json) records a successful run across all three upstreams for order 25. Gemini CLI is not a claimed passing client.

The [verification record](verification.md) lists executed checks, their environment and limitations. The [audit record](audit.md) maps corrections to reported findings. Historical passing results are evidence for those runs, not a substitute for checking a new change.

## CI and scope

The application CI job runs locked dependency installation, Python checks, disposable PostgreSQL concurrency cases, recovery and credential-preparation guards, and the dashboard build. Separate jobs lint/render the Helm chart and build all three images. The image job also probes the authentication memory budget in a constrained container.

The system deliberately uses one store, one gateway replica and process-local rate limits. Public deployment requires TLS, secure cookies, cluster Secret encryption and protected backups. Production values are a template; they do not demonstrate a deployed VPS. Schema migrations are forward-only until an explicit rollback migration exists.

Keep credentials, raw runtime evidence, recordings and the private assignment outside commits. Before sharing a demo, use the [demo checklist](demo.md) to review it for passwords, keys and invitation tokens.
