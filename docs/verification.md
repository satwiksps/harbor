# Verification record

Validation is performed against the local `k3d-urumi-commerce` cluster, not a public VPS. Run the commands below to reproduce the checks. Live acceptance tests create synthetic store data and consume Gemini quota.

## Independent audit corrections, 15 October 2026

The nine reported findings and their corrections are mapped in [the audit record](audit.md#independent-audit-follow-up). The corrected application is deployed as local Helm release 9. A private backup was taken before changing the existing local port bindings. The cluster server and all three persistent volumes were retained.

| Check | Result |
| --- | --- |
| `uv run pytest -q` on Windows | 145 passed in 41.00 seconds; three explicit platform/environment skips |
| Disposable PostgreSQL 17.11 concurrency | Both skipped database cases passed against separate contending connections: one invitation acceptance and one confirmation dispatch |
| Linux schema memory boundary | A counted-repeat regex that OOM-killed the old disposable 512 MiB container was rejected in 53 ms after the fix; normal validation then succeeded, with zero OOM events and a 123.78 MiB container peak |
| `uv run python scripts/verify_auth_budget.py` | At the chart's one CPU and 512 MiB budget: ten concurrent requests, two real Argon2 checks, eight 503 responses, 230.97 MiB peak, 78 ms maximum sampled health latency, no OOM kill |
| Ruff and production frontend build | Passed; no dependency changes were required |
| Helm and Kubernetes validation | Both profiles linted; local manifests passed server dry-run validation |
| Offline operational checks | Eight recovery cases, seven port-binding cases and two credential-preparation checks passed |
| Local socket exposure | Docker and Windows listeners use 127.0.0.1 for 8080, 8443 and the retained API port; connection attempts through both non-loopback host interfaces failed |
| Deployed `tests.acceptance` | All 13 non-LLM checks passed against release 9 |
| Deployed `tests.browser_ui` and `tests.browser_invitation` | Nine interaction checks and eight invitation/account-switching checks passed |
| Deployed `tests.browser_visual` | 51 captures passed at 320 to 1440 pixels, with no document-wide overflow or JavaScript exceptions |
| Deployed bundle with `tests.browser_accessibility` fixtures | Store link accessible at five widths; all reported normal-text combinations above 4.5:1, including the nested confirmation summary |
| `scripts/verify_network.py` | Positive controls, blocked peer/untrusted traffic, and named-Secret RBAC passed |

The schema worker has a Linux-only 128 MiB address-space limit; Windows tests deliberately skip that assertion. PostgreSQL tests deliberately skip without `TEST_POSTGRES_URL`. CI supplies a disposable PostgreSQL service and runs on Linux, so all three cases execute there. The image job also runs the real Argon2 budget probe. These are targeted resource regressions, not a sustained load or DDoS test.

For the isolated accessibility harness, set `COMMERCE_CONSOLE_URL` to the running console or frontend preview, then run `uv run python -m tests.browser_accessibility`. All API responses are browser-local fixtures; it sends no provider calls or application mutations. `tests.browser_acceptance --audit-only` now writes `audit-report.json` with scope `activity-only`; it never changes the full-journey `report.json`.

No live Gemini request, new checkout submission, backup restoration, public TLS/VPS test, or pod-failure drill was repeated during this correction pass. Earlier evidence for those flows remains explicitly separate below. Runtime screenshots and reports remain Git-excluded.

## Earlier end-to-end verification

| Check | Command | Evidence |
| --- | --- | --- |
| Python unit, authorization and real MCP HTTP tests | `uv run pytest -q` | 106 passed in 24.61 seconds on 15 October 2026 |
| Python static checks | `uv run ruff check backend upstreams tests scripts examples` | Passed |
| Frontend TypeScript and production bundle | `npm ci && npm run build` in `dashboard/` | Built inside the dashboard container |
| Helm local and production profiles | `helm lint deploy/chart -f deploy/values-local.yaml` and corresponding production values | Both passed |
| Local recovery and retained-credential guards | `pwsh -File scripts/test-local-recovery.ps1` and `uv run python scripts/test-secret-preparation.py` | Eight recovery scenarios and two secret-preparation checks passed; included in CI |
| Locked dependency audits | `pip-audit` against the frozen runtime export, `npm audit` and `npm audit --omit=dev` | Zero known vulnerabilities after updating OAuthLib to 4.0.0; 66 Python runtime packages audited |
| Private backup creation | `pwsh -File scripts/backup.ps1` | Completed before the audit migration, including databases, store files, current Kubernetes Secrets and local recovery files; restoration was not tested |
| Kubernetes resource schemas | Render chart, then `kubectl apply --dry-run=server` | Passed on the local API server |
| Deployed gateway acceptance | `uv run python -m tests.acceptance --chat` | All 14 checks passed against release 7; `.runtime/acceptance.json` |
| Dashboard interaction and failure recovery | `uv run python -m tests.browser_ui` | All nine checks passed on the release 7 bundle using real sign-in and browser response fixtures; `.runtime/browser-ui/report.json` |
| Browser checkout and chat flow | `uv run python -m tests.browser_acceptance` | Product 24 and order 25 verified; all eight checks passed in one complete run; `.runtime/browser/report.json` |
| Responsive presentation | `uv run python -m tests.browser_visual` | 51 layouts passed from 320 to 1440 px, with no page-wide horizontal overflow or JavaScript exceptions; `.runtime/visual-audit/report.json` |
| Browser invitation and account switching | `uv run python -m tests.browser_invitation` | All eight checks passed against release 7 on 15 October 2026; `.runtime/browser-invitation/report.json` |
| MCP Inspector and independent Gemini reference agent | `uv run python -m tests.external_agent --order-id 25` | Passed in 22.7 seconds, four real agent calls across all three upstreams; temporary key revoked; [sanitized evidence](assets/external-agent-evidence.json) |
| Network isolation and exact Secret RBAC | `uv run python scripts/verify_network.py` | Passed positive controls and blocked peer/untrusted access; `.runtime/network-verification.json` |
| Deleted connection survives restart | `uv run python scripts/verify_registry_lifecycle.py` | Release 7 passed; deletion persisted through gateway restart, upstream restarted independently, and original registration and permissions were restored; `.runtime/registry-lifecycle-verification.json` |
| Upstream failure isolation | `uv run python scripts/verify_failure.py` | Release 7: Unreachable status, clear call failure, other two upstreams healthy, discovery in 63 ms, and successful recovery; `.runtime/failure-verification.json` |
| Persistent data and compatible Helm rollback | `uv run python scripts/verify_release.py` | Data pods replaced, revision 4 rolled back to revision 3 configuration as revision 5; `.runtime/release-verification.json` |

Runtime reports and recordings stay Git-excluded because they may contain environment-specific data. The source harnesses are included. A report containing some successful checks is not a claim that its entire run passed; the command must exit successfully.

Final deployed browser checks covered desktop, tablet and mobile pages, expanded tool permissions, invitation and key dialogs, activity details, and keyboard navigation in a short landscape viewport. Store checks covered the catalog, original artwork and missing-media cards, a product page, a filled cart and checkout with the demo data-use link. Browser checks also confirm teal primary buttons on the product and checkout pages. The visual harness did not submit an order. A generated product from an interrupted test run was returned to draft; the successful run's product and order remain available.

## Coverage that matters

- Money stays decimal, today's order filters use Asia/Kolkata and include midnight and the final second, and order projections omit customer contact details. Streamed store responses have bounded byte counts and validated pagination.
- The MCP protocol check launches an actual HTTP upstream process and connects with the official SDK.
- Backend tests cover role-filtered discovery and direct calls, key revocation, audit ownership, single-use invitations, CSRF and confirmation ownership. Account-switching regressions verify session replacement, rejection of the old cookie and delegated chat credential, preservation of unrelated browser sessions, and rollback on failed invitation acceptance.
- Lifecycle tests cover first initialization, preserving a deleted seed connection across restart, and upgrading an existing database without restoring deleted connections.
- SSRF checks cover private destinations, DNS at connection time and redirect rejection. Request limits check actual chunked bytes. Compressed upstream responses are rejected before decompression can evade the byte limit.
- Untrusted schema validation prevents remote and filesystem reference retrieval while preserving literal `$ref` data. Discovery rejects ambiguous aliases and redacts raw and JSON-escaped credentials in metadata and results.
- Queueing and handshake regressions recheck revoked identity, changed permissions and changed targets. Chat reads must remain read-only; approvals bind the reviewed upstream revision, schema and permission. Credential rotation tests cover concurrent calls, lost responses, cancellation and deletion.
- Deployed acceptance uses both users, dynamic registry CRUD, per-tool permission changes, real tools from all three servers, stale names after disable/delete, and next-call revocation on an existing transport.
- Browser acceptance uses live Gemini to propose a product, approves it through the UI, checks out through WooCommerce, opens the order in WordPress admin and verifies that chat returns that actual order. It also exercises get-order and update-order-status through MCP.
- Invitation browser checks reproduce accepting a member invitation while signed in as admin. They verify the invited identity, removal of cached admin data and chat drafts in other tabs, fresh CSRF and member-owned keys, denied admin endpoints, an unaffected independent admin profile, reload and logout, used-link recovery through `/login`, and acceptance in a fresh browser. No JavaScript exceptions were observed. The regression fixes an old browser session being retained after account creation; the invited account's role was not elevated.
- External verification uses independently installed MCP Inspector and `examples/ask_gateway.py`, an independent client with no backend imports. The reference agent uses only the gateway URL, the member's key and a Gemini provider key. It has no shell or filesystem tools. Temporary member keys are revoked at the end. Gemini CLI compatibility is not claimed.

## Recovery and limits

The existing cluster was migrated to Kubernetes Secret encryption. Its final encryption status was Enabled with `reencrypt_finished` and matching hashes. Across full node restarts, both initial users, all three seeded product IDs, PVC identities and the WordPress configuration checksum were preserved.

The restart test exposed a stale k3d load-balancer address after the node's Docker IP changed. Restarting only the project load balancer restored connectivity; redeployment recomputes the API-server egress address. See operations for the local recovery procedure.

The earlier release 5 check preserved all three users, four products, order 18, all three PVC identities, and the published demo notice through replacement of the WordPress, MariaDB and PostgreSQL pods. A temporary gateway CPU-limit change was upgraded and rolled back to the corrected baseline. This verifies configuration rollback and data retention with unchanged images, not restoration of old application binaries or a database backup. The audit changes were deployed as release 7, with migration 0003 confirmed in the live PostgreSQL database.

The original Gemini 3.8 Flash choice timed out in bounded provider tests. Gemini 3.1 Flash-Lite returned a valid tool call in approximately seven seconds with the same primary key, so it became the configured default. Provider error responses remain visible; there is no fake fallback and no automatic key rotation.

This is not a load test, independent penetration test, HA test or disaster-recovery certification. Public TLS and VPS deployment require their own validation. The host has 16 GB of RAM; concurrent builds and browsers caused pressure during testing, so final integration work is serialized. A Helm rollback cannot restore database contents.
