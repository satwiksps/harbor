# Harbor

Harbor gives a team one place to access its WooCommerce tools. Connect services once, choose which tools members can use, then work through the web console or an external MCP client. Each tool call runs under the caller's identity and appears in Activity.

Use the [console](https://harbor.satwiksps.site/) with an invited account, browse the [sample store](https://store.harbor.satwiksps.site/), or [install Harbor locally](getting-started.md) with access to the source repository.

## How it works

**Model Context Protocol (MCP)** lets a client discover tools and call them with structured arguments. Harbor is an MCP **gateway**: it checks the caller's permissions and forwards each call to the connected service that provides the tool. These connected services are called **upstreams**.

For example, a member can ask for today's orders and a shipping estimate. An administrator can also create a product, after reviewing and approving the change in console chat. Both use the same gateway; members only receive tools they are allowed to use.

The bundled services are:

| Service | Tools and data |
| --- | --- |
| WooCommerce | Read products and orders; create products and update order status |
| Store policies | Read and search the sample store's shipping, returns and fulfillment policies |
| Shipping | Estimate delivery charges from the sample store's rate table |

The **Harbor Supply** storefront lets customers browse products, manage a cart and place a sample cash-on-delivery order. Its products and orders are the same records exposed through the WooCommerce tools.

## Choose a starting point

| Your task | Guide |
| --- | --- |
| Run Harbor on your computer | [Installation](getting-started.md) |
| Use chat, invite a member or manage access | [Using the console](user-guide.md) |
| Connect your own MCP client | [External MCP clients](external-clients.md) |
| Understand the components and request flow | [Architecture](system-design.md) |
| Find API routes and environment settings | [API and configuration](CONTRACT.md) |
| Change the code and run tests | [Development and testing](development.md) |
| Deploy, back up or recover Harbor | [Deployment and recovery](operations.md) |
| Understand permissions and security boundaries | [Security](threat-model.md) |

## Technology

The console uses React and TypeScript. A Python and FastAPI gateway connects to MCP servers through the official MCP SDK. PostgreSQL stores users, connections and activity; WooCommerce uses MariaDB and a separate WordPress content volume. A Helm chart deploys the system to Kubernetes, using k3d locally or k3s on a server.

Gemini powers optional console chat. The gateway, API keys, connection management and external MCP access work without a model provider key. Chat requires a configured key and available provider quota.

## Scope

Harbor supports one team, one WooCommerce store and one gateway replica. Tool access is role-based: administrators review and manage tools, while members use tools granted read access. Personal API keys identify each external client user. Browser OAuth for MCP clients and multi-replica operation are not implemented.

The sample storefront does not process payments, send email or book shipments. Its shipping quotes use a local rate table. Use fictional customer details when placing sample orders. A single-node deployment with local storage does not provide high availability.

These docs describe the application and its operation. The source repository is private, so source access is required to install or modify Harbor.
