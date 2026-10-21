# Harbor

Harbor lets a team use one WooCommerce store through a shared tool gateway. An administrator connects the services and reviews access. Members then use dashboard chat or an external client with their own identity and permissions.

**Model Context Protocol (MCP)** is the interface clients use to discover tools and call them with structured arguments. Harbor is the **gateway** that checks who is calling before forwarding a request. A connected **upstream** is a service behind that gateway, such as the WooCommerce adapter. You do not need to configure each upstream in every client.

For example, a member can read today's orders and ask for a shipping estimate. An administrator can also create a product, with an approval step in dashboard chat. Both users' tool calls appear in Activity.

Open the [live console](https://harbor.140-245-235-253.sslip.io/) with an invited team account, or browse the [demo storefront](https://store.140-245-235-253.sslip.io/). Both run on the same single-node deployment described in [operations](operations.md#vps-configuration).

## Start here

| I want to... | Read |
| --- | --- |
| Run the complete system locally | [Getting started](getting-started.md) |
| Use chat, invite a member or manage tool access | [User guide](user-guide.md) |
| Connect an external MCP client | [External clients](external-clients.md) |
| Change the code and run checks | [Development](development.md) |
| Understand the architecture and its tradeoffs | [System design](system-design.md) |
| Operate, back up or deploy the system | [Operations](operations.md) |
| Review the security boundary and remaining risks | [Threat model](threat-model.md) |
| Inspect what was tested and corrected | [Verification](verification.md) and [audit record](audit.md) |

## What runs

| Component | Responsibility |
| --- | --- |
| Harbor dashboard and gateway | Chat, team access, API keys, connections and activity |
| WooCommerce MCP server | Read products and orders; create products and update order status |
| Store policies MCP server | Read and search versioned sample policies |
| Shipping MCP server | Estimate delivery charges using the store's sample rate table |
| Harbor Supply storefront | Browse products and complete a cash-on-delivery checkout |

The React and TypeScript dashboard, Python gateway, three MCP servers and storefront run together in Kubernetes. PostgreSQL stores Harbor's records; MariaDB and a separate content volume store WordPress data.

## Capabilities

- A dynamic server registry with collision-safe tool names, cached discovery, isolated timeouts and credentials stored in Kubernetes Secrets.
- Invitations, administrator and member roles, individually hashed API keys, immediate revocation and per-tool role permissions.
- Gemini chat with bounded discovery for large catalogs, calls under the signed-in user's identity and confirmation before a write.
- Activity history and per-connection counts, failure rates, median and p95 latency, scoped to the viewer's access.
- SSRF protection, network isolation and least-privilege Kubernetes RBAC.
- One Helm chart with local and production values, repeatable bootstrap, backup scripts and CI checks.

## Scope and evidence

Local execution uses your computer and has no VPS dependency. Chat requires a Gemini API key and is subject to the provider's quota. A missing key leaves management and MCP available, with an explicit unavailable-chat notice.

The system supports one team, one store and one gateway replica. The storefront is a demonstration: it does not collect payments, send email or book carriers. The public Oracle VPS uses time-limited trial credits and trusted HTTPS. Its single node and local storage do not provide high availability. The [verification record](verification.md) distinguishes completed checks from remaining work, and the [threat model](threat-model.md) explains the security boundaries.

This website hosts documentation only. The application repository is private, so repository access is needed to inspect or run the source. Start with [installation](getting-started.md), or use the [technical walkthrough](demo.md) to review an already running system.
