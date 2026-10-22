<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/assets/harbor-banner-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset=".github/assets/harbor-banner.svg">
    <img src=".github/assets/harbor-banner.svg" alt="HARBOR: a team MCP gateway" width="666">
  </picture>
</p>

<p align="center">
  <strong>One gateway for your team's commerce tools.</strong>
</p>

<p align="center">
  A <a href="https://docs.harbor.satwiksps.site/#how-it-works">Model Context Protocol gateway</a> for WooCommerce with <a href="https://docs.harbor.satwiksps.site/user-guide/#roles-and-permissions">personal keys and role-based access</a>.
</p>

<table align="center" width="100%">
  <tr>
    <td align="center" valign="top" width="33%">
      <picture><source media="(prefers-reduced-motion: reduce)" srcset=".github/assets/live-dot-static.svg"><img src=".github/assets/live-dot.svg" alt="Live" width="12" height="12"></picture><br>
      <strong>Store</strong><br>
      <a href="https://store.harbor.satwiksps.site/">https://<wbr>store.<wbr>harbor.<wbr>satwiksps.<wbr>site/</a>
    </td>
    <td align="center" valign="top" width="33%">
      <picture><source media="(prefers-reduced-motion: reduce)" srcset=".github/assets/live-dot-static.svg"><img src=".github/assets/live-dot.svg" alt="Live" width="12" height="12"></picture><br>
      <strong>Console</strong><br>
      <a href="https://harbor.satwiksps.site/">https://<wbr>harbor.<wbr>satwiksps.<wbr>site/</a>
    </td>
    <td align="center" valign="top" width="33%">
      <picture><source media="(prefers-reduced-motion: reduce)" srcset=".github/assets/live-dot-static.svg"><img src=".github/assets/live-dot.svg" alt="Live" width="12" height="12"></picture><br>
      <strong>Docs</strong><br>
      <a href="https://docs.harbor.satwiksps.site/">https://<wbr>docs.<wbr>harbor.<wbr>satwiksps.<wbr>site/</a>
    </td>
  </tr>
</table>

## Run locally

Clone this private repository, then open PowerShell at its root. [Prerequisites](https://docs.harbor.satwiksps.site/getting-started/#prerequisites): PowerShell 7, Docker Desktop with Linux containers and 8 GB allocated, Git, kubectl, Python 3.12 and uv.

```powershell
uv sync --frozen
pwsh -File scripts/bootstrap.ps1
```

[Bootstrap](https://docs.harbor.satwiksps.site/getting-started/#start-the-system) installs pinned Helm/k3d and builds the system. Open [the local console](http://harbor.localhost:8080) or [store](http://store.localhost:8080); find separate Harbor and WordPress [credentials](https://docs.harbor.satwiksps.site/getting-started/#sign-in) in `.secrets/development.json`. [Optional Gemini setup](https://docs.harbor.satwiksps.site/getting-started/#configure-optional-chat) enables chat and requires available provider quota.

## Deploy on a VPS

1. [Prepare a dedicated Linux host](https://docs.harbor.satwiksps.site/operations/#prepare-the-public-host): install k3s with Secret encryption, Helm and cert-manager; configure console/store DNS and firewall rules.
2. [Build and import a release](https://docs.harbor.satwiksps.site/operations/#deploy-a-reviewed-release), then run `sudo bash scripts/deploy-vps.sh CONSOLE_HOST STORE_HOST ADMIN_EMAIL TAG`. The helper generates fresh production credentials on first installation.
3. Review [production values](https://docs.harbor.satwiksps.site/operations/#manual-deployment-configuration): DNS, TLS ingress, storage class/sizes and existing Secrets. [Verify the deployment](https://docs.harbor.satwiksps.site/development/#testing-a-public-deployment) and configure [backups](https://docs.harbor.satwiksps.site/operations/#create-a-backup).

## Use Harbor

- **[Place an order](https://docs.harbor.satwiksps.site/user-guide/#use-the-sample-store):** add a product to the cart, review quantities and checkout with fictional details and an Indian address. Choose Cash on delivery. No payment or shipment occurs.
- **[Use chat](https://docs.harbor.satwiksps.site/user-guide/#use-chat-and-review-writes):** sign in and ask `What orders came in today?`. Administrators review and approve proposed writes; members use permitted read tools.
- **[Connect an external client](https://docs.harbor.satwiksps.site/external-clients/#connect-a-client):** create a [personal key](https://docs.harbor.satwiksps.site/user-guide/#create-an-api-key), select Streamable HTTP, and set `http://harbor.localhost:8080/mcp` with `Authorization: Bearer <KEY>`. Hosted endpoint: `https://harbor.satwiksps.site/mcp`. [MCP Inspector](https://docs.harbor.satwiksps.site/external-clients/#mcp-inspector) can list and call tools without Gemini.

## Source and design

| Component | Source |
| --- | --- |
| React/TypeScript dashboard | [dashboard/](dashboard/) |
| Python/FastAPI backend and MCP gateway | [backend/](backend/), [gateway.py](backend/gateway.py) |
| Custom WooCommerce MCP server | [commerce.py](upstreams/commerce.py), [server.py](upstreams/server.py) |
| Chat agent and independent MCP client | [chat.py](backend/chat.py), [ask_gateway.py](examples/ask_gateway.py) |
| Helm chart and environment values | [chart/](deploy/chart/), [local](deploy/values-local.yaml), [production](deploy/values-prod.yaml) |

[Architecture](https://docs.harbor.satwiksps.site/system-design/#components) keeps identity, registry and chat together to simplify a single-team deployment. [PostgreSQL](https://docs.harbor.satwiksps.site/system-design/#technology-stack) stores application state; WooCommerce owns commerce data and exposes it through its [REST API](https://docs.harbor.satwiksps.site/system-design/#store-correctness). [Aggregation and routing](https://docs.harbor.satwiksps.site/system-design/#discovery-routing-and-failures) use collision-safe aliases, independent catalogs, deadlines and permission checks. Failed upstreams do not block others; uncertain writes are not retried automatically.

The [deployment tradeoff](https://docs.harbor.satwiksps.site/system-design/#local-and-production) is one replica per component, node-local storage and process-local rate limits. OAuth, image-based server provisioning, multi-store tenancy and horizontal scaling are outside the implemented scope.

## Security boundaries

[Members](https://docs.harbor.satwiksps.site/threat-model/#identity-and-permissions) can read records exposed by allowed tools without seeing shared credentials; they cannot manage teammates or inspect another user's keys or Activity. Administrators control team access. [Client tokens stay at the gateway](https://docs.harbor.satwiksps.site/threat-model/#registered-servers-and-network-access); upstreams receive only their configured credential, preventing them from reusing a member's gateway token. [Untrusted tool descriptions](https://docs.harbor.satwiksps.site/threat-model/#chat-and-write-approval) can manipulate model choices, so authorization remains server-side and dashboard writes require approval. External administrator clients can write directly.

## Implemented stand-out features

| Feature | Implementation and limits |
| --- | --- |
| VPS deployment | [Same Helm chart on k3s, DNS and HTTPS](https://docs.harbor.satwiksps.site/operations/#vps-configuration) |
| Tool permissions | [Role-based discovery filtering and direct-call rejection](https://docs.harbor.satwiksps.site/threat-model/#identity-and-permissions) |
| Large catalogs | [Search before schema selection](https://docs.harbor.satwiksps.site/system-design/#large-tool-catalogs-in-chat); [500 synthetic tools tested](tests/backend/test_catalog.py) |
| Security hardening | [SSRF protection and NetworkPolicies](https://docs.harbor.satwiksps.site/threat-model/#registered-servers-and-network-access), [restricted RBAC and encrypted Secrets](https://docs.harbor.satwiksps.site/threat-model/#secrets-and-audit-data), [non-root containers](deploy/chart/templates/) |
| Observability | [Per-server counts, failure rate, p50/p95 latency and attributed errors](https://docs.harbor.satwiksps.site/user-guide/#read-activity-and-recover-from-errors) |
| Upgrade and rollback | [Application release checks](https://docs.harbor.satwiksps.site/operations/#test-an-application-upgrade-and-rollback); [data restoration is separate](https://docs.harbor.satwiksps.site/operations/#restore-data) |
