# Commerce console

A team MCP gateway for a WooCommerce store. Administrators connect tools once; members use one endpoint or the dashboard chat with their own identity and permissions.

The React and TypeScript dashboard sits on a Python, FastAPI and MCP gateway. The store, gateway and three original MCP servers run together in Kubernetes.

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

## What is included

- A persistent WordPress and WooCommerce storefront with cash-on-delivery checkout.
- Three Streamable HTTP MCP servers: commerce, store policies and shipping quotes. The commerce server implements five product and order tools.
- A dynamic server registry with collision-safe tool names, cached discovery, isolated timeouts and credentials stored in Kubernetes Secrets.
- Invitations, administrator and member roles, individually hashed API keys, immediate revocation and per-tool role permissions.
- Gemini chat that calls the gateway as the signed-in user, shows tool results and asks for confirmation before a write.
- An attributable audit trail, SSRF protection, network isolation and least-privilege Kubernetes RBAC.
- One Helm chart with local and production values, repeatable bootstrap, backup scripts and CI checks.

The project focuses on one store and one gateway replica, with per-tool permissions and security controls around every gateway call.

## Scope and evidence

Local execution uses your computer and has no VPS dependency. Chat requires a Gemini API key and is subject to the provider's quota. A missing key leaves management and MCP available, with an explicit unavailable-chat notice.

This is a single-node demonstration with production deployment guidance. It does not claim high availability or an already deployed public VPS. The [verification record](verification.md) separates local checks, historical end-to-end evidence and work that has not been repeated. The [threat model](threat-model.md) describes the remaining operational limits.

The application repository is private. Repository access is needed to inspect or run its source. The [API and environment reference](CONTRACT.md), [visual provenance](brand-notes.md) and [technical walkthrough](demo.md) provide supporting detail.
