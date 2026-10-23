# Using the console

Sign in with the credentials created during [installation](getting-started.md#sign-in), or accept an invitation from an administrator. Harbor does not have public self-registration.

| Page | What you can do |
| --- | --- |
| **Chat** | Ask about the store and review proposed changes |
| **Connections** | See connected services and available tools; administrators also manage them |
| **Team & keys** | Administrators invite users, manage roles and issue API keys |
| **My API keys** | Members create and revoke their own API keys |
| **Activity** | Inspect tool calls and connection performance |

On a phone, open the navigation menu to switch pages. The sign-out button sits beside your account details at the bottom of the navigation. Harbor and WordPress use separate accounts.

## Roles and permissions

The bundled connections start with these permissions:

| Action | Administrator | Member |
| --- | --- | --- |
| Read products, orders, policies and shipping estimates | Yes | Yes, for tools marked as read |
| Create products and update orders | Yes | No |
| View connection summaries | Yes | Yes |
| Register, edit or disable upstream servers | Yes | No |
| Change tool permissions or manage the team | Yes | No |
| Create and revoke API keys | Own keys or another member's keys | Own keys |
| View activity | All users | Own activity |

These rules are enforced by the backend. Newly discovered tools default to administrator-only until an administrator reviews them and marks them as read tools. A read permission means the tool is made available to members; only assign it to a tool whose behavior you trust to be read-only.

The approval dialog belongs to dashboard chat. An external client using an administrator key can call write tools directly. Use a member key for a client that should only read data.

## Use chat and review writes

Chat uses the tools available to the signed-in user. Start with a read, such as:

> What products are in the store?

The starter cards place a suggested question in the message box so you can edit it before sending. Replies support headings, lists, tables and code. Images appear as links. If you scroll back while a reply arrives, your reading position stays in place; use **Jump to latest** to return to the newest message.

Tool calls and their results remain visible in the conversation. An administrator can propose a change:

> Create a product called Field notebook priced at INR 249, SKU FIELD-NOTEBOOK-01.

Review the proposed tool and arguments before approving. Choose a SKU that is not already in the store. Confirmations are tied to the user session, expire after five minutes and can be used only once. The gateway checks the target and current permissions again before dispatch.

If a write times out, check the store before trying again. A lost response does not prove that the store rejected the change, and the gateway does not automatically retry writes.

Conversation content stays in memory in the current browser tab. Reloading, signing out or starting a new chat clears it; switching workspace pages does not. Completed tool activity remains in PostgreSQL. Long chats send only a bounded recent context to Gemini, so restate an older detail if it is needed for the next request.

### Provider rate limits

If Gemini returns a quota error, chat shows how long to wait before trying again. Harbor pauses new model requests across users of that gateway process during this cooldown. It uses the provider's retry hint when available, otherwise 60 seconds. A cooldown ending does not guarantee that daily quota has reset. Connections, Activity and [direct MCP calls](external-clients.md#mcp-inspector) remain available without Gemini.

[Gemini quotas](https://ai.google.dev/gemini-api/docs/rate-limits) apply per project, not per API key. Check the configured project's limits in Google AI Studio. Extra keys do not increase that project's allowance. To use less quota, ask focused questions, request smaller result pages, and start a new chat when earlier context is no longer needed. Harbor does not switch accounts or enable billing automatically.

Chat accepts three simultaneous requests per gateway process. If all slots are occupied, it asks you to retry shortly instead of leaving another request waiting. Writes are never automatically repeated after a provider or connection failure.

## Use the sample store

1. Open the storefront from the console's **Open store** link. Browse the catalog or a product page and choose **Add to cart**.
2. Open the cart to review quantities, remove products and see the shipping estimate.
3. Proceed to checkout with fictional customer details and an Indian address, such as Bengaluru, Karnataka, 560038.
4. Choose **Cash on delivery** and place the order. No money is collected and no shipment is booked.
5. Return to console chat and ask: `What orders came in today?` If you have a WordPress administrator account, you can also inspect the order under **WooCommerce > Orders**.

The store uses INR and the Asia/Kolkata timezone. It has no outbound email provider. Products created through an approved chat request become available in this same store.

## Invite a member

In **Team & keys**, create a member invitation and copy its link. Invitations are delivered by copying the link; the application does not send email. They expire after two days and can be accepted once.

Accepting an invitation signs in as that invited account, replacing any existing session in the same browser profile. Other tabs refresh their identity and clear the previous account's workspace state. Use a separate browser profile if you need administrator and member sessions open simultaneously.

An invalid, expired or used link leaves the current session unchanged. An existing member can sign in through `/login`. Keep invitation links private because their tokens allow account creation. The invited person chooses a display name and a password of at least 12 characters.

## Create an API key

Open **My API keys** as a member, or **Team & keys** as an administrator. Select **Create key**, give it a recognizable name and copy the full key when it appears. The complete key is shown only once. Administrators can also issue a key for another active user; that key uses the selected user's permissions.

An external client needs the gateway's `/mcp` URL and the key as a bearer credential. It does not receive store or upstream credentials. Keep the key private, and revoke it when the client no longer needs access.

Follow the [external client guide](external-clients.md) for MCP Inspector and the standalone reference agent. A question that uses two connections is:

> Read the shipping policy and quote delivery for an INR 1700 order to Karnataka, India. Use both tools.

This combines the Store policies and Shipping connections. Shipping quotes use the sample store's rate table, not a carrier integration. Ask for the shipping policy to see the current rules and free-shipping threshold.

## Manage connections and access

Administrators manage upstreams and their tool permissions in **Connections**:

1. Add a connection with a name, a unique slug, its MCP URL and the authentication it requires. A slug is a short identifier such as `inventory`. Public destinations must use HTTPS; internal destinations must be approved in deployment configuration.
2. Check the connection and expand its tools. The slug becomes part of each external tool name and cannot be changed later.
3. Review each tool before granting **Read access**. **Write access** keeps it restricted to administrators and requires approval in dashboard chat.
4. Save permission changes, then refresh the external client's catalog if it is already connected.

Use the tool search inside a connection to find a tool by name or description. Filtering does not discard unsaved permission changes, including changes to rows hidden by the current search. The change count and **Save permissions** button show when a save is still needed.

Connection status refreshes while the page is visible. Disabling a connection removes its tools from discovery; removing it also deletes the registration. Both preserve past activity. Stale direct calls are rejected even if a client retains a cached tool name. A blank credential field during editing preserves the existing credential; use the explicit removal option to clear it.

Revoke a key in **Team & keys** or **My API keys** when it is no longer needed. Its next request fails, including requests on an initialized MCP transport. An operation that has already been dispatched may still finish. The system also prevents demoting or deactivating the last active administrator.

The team list shows active members by default. Use **Show deactivated members** to inspect or reactivate an earlier account. Keys and invitations also have history controls, so completed records remain available without crowding the active view.

## Read activity and recover from errors

Use **Activity** to inspect tool calls, their outcomes and performance. Members see only their own activity; administrators can inspect the team or select a teammate. Connection and teammate filters apply to both the overview and history.

The overview shows calls started within the last hour, 24 hours or seven days. It reports call counts, failure rate, median and p95 latency, with a breakdown by connection and outcome. Median is the middle response time; p95 is the time within which approximately 95% of measured calls finished. **Refresh** updates both sections; the overview shows when it was measured.

History shows calls across all time, regardless of the overview's time window. Its outcome filter does not change the overview. Open **Details** for a call's request ID and error.

Use **View history** at the top to jump directly to the call list. The per-connection metrics table can scroll sideways when the available width is limited.

Latency measures the complete gateway call, including waiting, connection setup and execution. Denied, invalid, cancelled and in-progress calls count toward activity but do not enter failure-rate or latency calculations. **No data** means there is no eligible measurement. Small samples can vary widely. For exact calculations, see the [activity API reference](CONTRACT.md#activity).

| What you see | What to do |
| --- | --- |
| Chat is not configured | Ask the operator to configure the provider key; connections and external MCP remain available |
| Provider quota error | Wait for the provider's quota to become available; external MCP tools do not need Gemini |
| A call timed out | Check the store and Activity before repeating a write; for a large read, request fewer records |
| Connection is Unreachable | Inspect its error and check the service; other connections can still work |
| Activity overview is unavailable | Use **Try again**; the history can remain available while summary metrics are unavailable |
| No calls in this window | Choose a longer window or change your filters |
| Approval expired or the tool changed | Make a new request and review a fresh proposal |
| Key was revoked or the session expired | Sign in again or create a new permitted key |

For service and deployment failures, use [Troubleshooting](operations.md#troubleshooting). For permission boundaries, read [Security](threat-model.md).
