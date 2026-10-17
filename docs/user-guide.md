# User guide

The console has four workspaces: **Chat**, **Connections**, **Team & keys** and **Activity**. Members see **My API keys** in place of Team & keys. Sign in with the credentials created during [local setup](getting-started.md), or accept an invitation from an administrator.

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

Tool calls and their results remain visible in the conversation. An administrator can propose a change:

> Create a product called Field notebook priced at INR 249, SKU FIELD-NOTEBOOK-01.

Review the proposed tool and arguments before approving. Use a unique SKU for each demonstration. Confirmations are tied to the user session, expire after five minutes and can be used only once. The gateway checks the target and current permissions again before dispatch.

If a write times out, check the store before trying again. A lost response does not prove that the store rejected the change, and the gateway does not automatically retry writes.

Conversation content stays in memory in the current browser tab. Reloading, signing out or starting a new chat clears it; switching workspace pages does not. Completed tool activity remains in PostgreSQL. Long chats send only a bounded recent context to Gemini, so restate an older detail if it is needed for the next request.

## Complete a store order

1. After an approved product creation succeeds, inspect the returned product and open the storefront.
2. Add the product to the cart and check out with a fictional Indian address, such as Bengaluru, Karnataka, 560038.
3. Choose **Cash on delivery**. This is a demo store; no money or shipment is processed.
4. Open WordPress administration and find the order under **WooCommerce > Orders**.
5. Return to chat and ask: `What orders came in today?`

The store uses INR and the Asia/Kolkata timezone. Use synthetic data for checkout. The sample store has no outbound email provider.

## Invite a member

In **Team & keys**, create a member invitation and copy its link. Invitations are delivered by copying the link; the application does not send email. They expire after two days and can be accepted once.

Accepting an invitation signs in as that invited account, replacing any existing session in the same browser profile. Other tabs refresh their identity and clear the previous account's workspace state. Use a separate browser profile if you need administrator and member sessions open simultaneously.

An invalid, expired or used link leaves the current session unchanged. An existing member can sign in through `/login`. Keep invitation links private because their tokens allow account creation. The invited person chooses a display name and a password of at least 12 characters.

## Connect an external client

As the member, create an API key in **My API keys**. Copy it when it is displayed; the complete key is shown once. An external client needs only the gateway's `/mcp` URL and that key as a bearer credential. It does not receive store or upstream credentials.

Follow the [external client guide](external-clients.md) for MCP Inspector and the standalone reference agent. As a multi-server example, ask:

> Read the shipping policy and quote delivery for an INR 1700 order to Karnataka, India. Use both tools.

This combines the policy and shipping upstreams. The sample table starts at INR 59 for standard regions or INR 99 for remote regions, plus INR 20 for each additional started 0.5 kg above the first 0.5 kg. Orders of INR 1500 or more ship free within India. The storefront uses the same sample rules. Shipping quotes are estimates from that table, not a carrier integration.

## Manage connections and access

Administrators manage upstreams and their tool permissions in **Connections**:

1. Add a connection with a unique slug, its MCP URL and the authentication it requires. Public destinations must use HTTPS; internal destinations must be approved in deployment configuration.
2. Check the connection and expand its tools. The slug becomes part of each external tool name and cannot be changed later.
3. Review each tool before granting **Read access**. **Write access** keeps it restricted to administrators and requires approval in dashboard chat.
4. Save permission changes, then refresh the external client's catalog if it is already connected.

Connection status refreshes while the page is visible. Disabling a connection removes its tools from discovery; removing it also deletes the registration. Both preserve past activity. Stale direct calls are rejected even if a client retains a cached tool name. A blank credential field during editing preserves the existing credential; use the explicit removal option to clear it.

Revoke a key in **Team & keys** or **My API keys** when it is no longer needed. Its next request fails, including requests on an initialized MCP transport. An operation that has already been dispatched may still finish. The system also prevents demoting or deactivating the last active administrator.

## Read activity and recover from errors

Use **Activity** to inspect tool calls, their outcome and duration. Members see only their own activity; administrators can inspect all users. Filter by connection or outcome and expand a record for the request ID and error. This audit trail is distinct from conversation history.

| What you see | What to do |
| --- | --- |
| Chat is not configured | Ask the operator to configure the provider key; connections and external MCP remain available |
| Provider quota or timeout error | Wait or try a smaller read; check store state before repeating a write |
| Connection is Unreachable | Inspect its error and check the service; other connections can still work |
| Approval expired or the tool changed | Make a new request and review a fresh proposal |
| Key was revoked or the session expired | Sign in again or create a new permitted key |

For a complete presentation, use the [demo walkthrough](demo.md). For permission boundaries and remaining risks, read the [threat model](threat-model.md).
