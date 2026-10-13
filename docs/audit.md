# Audit and corrective work

Audit date: 15 October 2026. Scope: application logic, authorization, chat, MCP routing, WooCommerce, local operations and responsive presentation. This is an engineering review with automated and manual checks, not an independent penetration test or a guarantee that no defects remain.

## Corrected behavior

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

## Validation method

Unit and real MCP protocol tests cover authorization, race conditions, schema validation, response boundaries and store calculations. Deployed acceptance exercises real Kubernetes services and the actual store. Browser regression checks use real sign-in and controlled responses to reproduce network failures and overlapping actions. These fixture checks are reported separately from live Gemini and checkout tests.

Responsive inspection covers 320, 390, 768, 1024 and 1440 px layouts, including navigation, tool permissions, activity details, invitation and API-key dialogs, product pages, cart and checkout. Screenshots and detailed runtime reports remain Git-excluded. See the [verification record](verification.md) for executed commands and results.

Source scanning checks for committed private files, known credential values, broken documentation links and em dashes in authored text. Dependency auditing covers the locked Python runtime and npm dependency tree. The OAuthLib update addresses the [upstream PKCE advisory](https://github.com/oauthlib/oauthlib/security/advisories/GHSA-xpv3-w29h-x7cv); this application uses OAuth 1.0 signing, not the affected OAuth 2.0 PKCE flow.

## Remaining boundaries

The tested deployment is local and single-node. Public TLS, a VPS deployment, sustained load, browser engines other than Chromium, and restoration from a backup need separate validation. The prior pod replacement and Helm configuration rollback checks do not prove database backup restoration. Free Gemini quota and availability remain external dependencies.

The repository remains private. Reviewer access and a human-narrated demonstration must be arranged before submission. No email delivery, carrier integration, high availability or automated submission is presented as implemented functionality.
