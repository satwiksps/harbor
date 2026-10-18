# Audit and corrective work

Scope: application logic, authorization, chat, MCP routing, WooCommerce, local operations and responsive presentation. Findings are grouped by review date. This is an engineering review with automated and manual checks, not an independent penetration test or a guarantee that no defects remain.

## Reliability and documentation review, 20 October 2026

| Area | Finding and correction |
| --- | --- |
| Fresh cluster startup | Asking k3d for API port `0` left its metadata and kubeconfig pointing to port `0` even after Docker assigned a usable port. Setup now chooses a free loopback port before creation and synchronizes kubeconfig with the observed binding. A real cluster creation reached Ready, and the local binding and recovery guards passed. |
| Account email validation | The API accepted malformed email addresses that the browser would reject during sign-in. Account inputs now validate ordinary ASCII mailbox syntax consistently, with regression cases for invalid addresses. |
| Upstream DNS resilience | Connections tried only the first resolved address, so one unreachable address could hide a healthy upstream. The transport now tries validated numeric addresses within one shared connection deadline. Private-address rejection and TLS server-name validation remain enforced. |
| Schema worker startup | Python process startup and validator imports could consume the schema execution deadline before validation began. A separate five-second readiness limit now covers startup while preserving the two-second untrusted execution limit and the outer request deadline. |
| Checkout shipping | Floating-point addition could place an exact 1.5 kg package in the next rate band. Checkout normalizes the computed weight before rounding up. Nine PHP regression cases and the running WooCommerce hook verify exact boundaries, remote rates and pound-to-kilogram conversion. |
| Interface recovery | Connection loading failures lacked a clear retry path, and a failed sign-out could hide the still-active session. Connection retry is explicit and unsuccessful sign-out keeps the workspace visible with an error. Member copy, long labels and modal layouts were also checked at five widths. |
| Empty chat and team history | Automatic scrolling could move an empty conversation away from its welcome state. Empty chat now starts at the top. Team views default to active members, with an explicit toggle for deactivated accounts instead of a crowded historical list. |
| Documentation contrast | A mobile style made every drawer heading white, including headings on a light background. White text is now limited to the teal Harbor header; section headings and back arrows use the dark foreground at 5.21:1 contrast. |
| First-run guidance | The introduction assumed MCP knowledge, the external-client guide led with test harnesses, and member instructions used the administrator's navigation label. Guides now define the components, give observable setup checks and follow the actual role-specific interface. |
| Permission boundaries | Chat approval could be mistaken for a gateway-wide requirement. The user guide, API reference and security documentation now explicitly distinguish chat confirmations from direct external administrator calls. |
| Operational guidance | Long sections mixed procedures with duplicated historical results. Maintenance steps now have distinct headings, troubleshooting starts with symptoms, and dated evidence remains in the verification record. Restore guidance explicitly keeps the store bootstrap Job stopped during imports. |
| Recovery ownership | Manually applied recovery manifests left fields shared with a temporary Kubernetes field manager. A forced Helm conflict resolution did not transfer unchanged fields. A scoped cleanup removed only that temporary manager's entries, preserving resource specifications and content. Normal deployment and the configuration upgrade/rollback drill then passed; deployment defaults are unchanged. |
| Repository cleanup | Removed an unused private screenshot from `docs/assets` and a dashboard-specific Docker ignore file that the root-context builds never used. Runtime screenshot evidence stays ignored, and documentation publication still rejects unreviewed files. |
| CI maintenance | The successful run reported that the dependency setup action targeted a deprecated Node runtime. The workflow now uses setup-uv v7 and names Ubuntu 24.04 explicitly so an `ubuntu-latest` migration does not silently change the test environment. |

The [verification record](verification.md) separates automated regressions, manual presentation checks and real checkout/provider results. The current run also restored both databases and WordPress content from backup into empty volumes before exercising the deployed application.

## Corrected behavior, 15 October 2026

| Area | Finding and correction |
| --- | --- |
| Invitations | Accepting a member invitation while signed in retained the old administrator session. Acceptance now replaces that session, invalidates its delegated chat authority and refreshes the identity in other tabs. Independent browser profiles stay signed in. |
| Tool approval | A connection, credential, schema or permission could change between proposal and execution. Calls and confirmations now bind the intended target and recheck authority after waits. Automatic chat calls must still be read-only at dispatch. Old pending approvals require a new proposal after the migration. |
| Registry concurrency | Concurrent connection edits, deletion and credential reads could mix revisions or restore a deleted credential reference. Registry mutations and credential snapshots now share a short lock; upstream network work runs outside it. Editing an unchanged endpoint also preserves reviewed tool permissions. |
| Untrusted MCP data | Schema references could trigger unintended retrieval, tool names could collide after aliasing, and escaped credential text could bypass redaction. Validation forbids remote retrieval, discovery rejects ambiguous aliases, and redaction covers raw and JSON-escaped credentials in metadata and results. |
| Store results | Exclusive REST date bounds could omit orders at the first or last second of a day. The adapter expands the bounds correctly, validates response shapes and pagination, and limits bytes while streaming. Failed writes explicitly describe uncertain outcomes. |
| Authentication input | Non-ASCII bearer headers could produce a server error. They now receive an authentication failure. Blank names and malformed product data are rejected before use. |
| Chat usability | Failed sends now offer a retry without duplicate user messages. Expired or changed approvals release the composer. Long conversations keep visible history while sending a bounded recent context. Transient health-check failures preserve the current workspace. |
| Activity and connections | Changing activity filters no longer leaves stale rows visible after failure. Independent connection actions keep their own pending state. Permission controls remain locked during a save. |
| Mobile and accessibility | Navigation traps and restores focus, responds to Escape, closes when selecting the current page, scrolls on short landscape screens and releases the main content on resize. Small controls and text were enlarged, narrow permission rows stack, and form controls avoid mobile input zoom. |
| Team records | Active keys and pending invitations are the default view. Explicit history controls retain access to revoked keys and used invitations without crowding everyday work. |
| Store presentation | New products without uploaded media receive an accessible, clearly labeled typographic card. Seed artwork and uploaded images remain intact. The store introduction is centered and narrow checkout layouts were inspected. |
| Repeatability and recovery | Bootstrap checks retained state before generating credentials, recovers a stale project load balancer with a bounded check, and preserves store edits after initial seeding. Backups include current mutable credential references and local recovery files, with private permissions and atomic dump completion. |
| Logging and dependencies | Dashboard request logging no longer records invitation query tokens, and responses use a no-referrer policy. OAuthLib was updated to 4.0.0 after the dependency audit. |

## Independent audit follow-up

The subsequent independent report identified nine actionable findings. Each was checked against the implementation before correction. The availability findings were reproduced in isolated tests, including a disposable container for the memory budget. No application data was used for destructive probes.

| Finding | Verified correction | Regression coverage |
| --- | --- | --- |
| AUD-001: hostile schemas | Schema checking and validation run in one reusable, terminable subprocess. Each operation has a two-second execution deadline, bounded JSON input, and a Linux worker memory limit. | Hostile regexes, reference expansion, cancellation, worker recovery, concurrent health and a healthy upstream |
| AUD-002: password memory | Hashing and verification share two non-queueing slots. Overload returns 503 with Retry-After; Argon2 strength is unchanged. The request limiter also synchronizes concurrent handlers. | Ten concurrent HTTP attempts, invitation/hash slot sharing, failure cleanup, disposable 512 MiB container |
| AUD-003: local publication | New clusters explicitly bind ingress and API ports to loopback. Existing installations have a scoped, resumable load-balancer migration that preserves the server and data volumes. | Seven offline binding scenarios, actual port migration, Windows listeners and non-loopback connection rejection |
| AUD-004: proxy identity | Uvicorn trusts the ingress node's pod CIDRs only in conjunction with the Traefik NetworkPolicy. Deployment discovers the local range. | Independent login/invitation allowances, successful second-client login, forged headers and forwarded chains |
| AUD-005: external read-only agent | Every request carries the gateway's read-only guard, including when an administrator key is used accidentally. | Mocked model response after reclassification over the real MCP transport; zero upstream dispatches |
| AUD-006: uncertain writes | Failures after write dispatch consistently advise reconciliation before retry, including oversize, protocol, serialization, tool error, timeout and cancellation paths. | One-dispatch assertions, audit errors, read-only controls and pre-dispatch failure controls |
| AUD-007: mobile store link | The link has a persistent accessible name. | Accessible-role queries from 320 to 1440 pixels |
| AUD-008: text contrast | Four affected foreground treatments were darkened without changing the layout or palette. | Rendered foreground/background contrast at five widths, all above 4.5:1 |
| AUD-009: evidence integrity | Activity-only verification writes a separate scoped report and never alters the full-journey result. | Partial, complete and missing prior reports, with successful and failed Activity checks |

Additional PostgreSQL tests use separate connections and synchronized competing requests to verify single-use invitation acceptance and confirmation approval. These tests do not substitute for a full live checkout or provider demonstration. CI supplies a disposable PostgreSQL service and runs the authentication container budget check.

## Validation method

Unit and real MCP protocol tests cover authorization, race conditions, schema validation, response boundaries and store calculations. Deployed acceptance exercises real Kubernetes services and the actual store. Browser regression checks use real sign-in and controlled responses to reproduce network failures and overlapping actions. These fixture checks are reported separately from live Gemini and checkout tests.

Responsive inspection covers 320, 390, 768, 1024 and 1440 px layouts, including navigation, tool permissions, activity details, invitation and API-key dialogs, product pages, cart and checkout. Screenshots and detailed runtime reports remain Git-excluded. See the [verification record](verification.md) for executed commands and results.

Source scanning checks for committed private files, known credential values, broken documentation links and em dashes in authored text. Dependency auditing covers the locked Python runtime and npm dependency tree. The OAuthLib update addresses the [upstream PKCE advisory](https://github.com/oauthlib/oauthlib/security/advisories/GHSA-xpv3-w29h-x7cv); this application uses OAuth 1.0 signing, not the affected OAuth 2.0 PKCE flow.

## Remaining boundaries

The tested deployment is local and single-node. Public TLS, a VPS deployment, sustained load and browser engines other than Chromium need separate validation. Backup restoration was exercised locally on 20 October; that result does not establish recovery time or portability to every production environment. Free Gemini quota and availability remain external dependencies.

The application repository remains private. No email delivery, carrier integration or high availability is presented as implemented functionality.
