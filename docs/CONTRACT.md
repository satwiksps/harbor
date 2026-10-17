# API and environment reference

The browser uses REST routes under `/api` and sends cookies with `credentials: include`. External MCP clients use Streamable HTTP at `/mcp`, authenticated with a personal bearer key. A gateway key does not authenticate the browser management API. Application JSON keys use `snake_case`; MCP tool schemas retain the protocol's `inputSchema` field.

Application errors use `{"detail":"human readable message"}`. Unknown request fields are rejected. Credential values and passwords are never returned in resource summaries. A newly created API key is returned once by its creation endpoint.

## Request conventions

| Status | Meaning |
| --- | --- |
| `401` | Missing, expired, revoked or invalid authentication |
| `403` | Insufficient role, invalid CSRF token or disallowed browser origin |
| `404` | Requested record or invitation was not found |
| `409` | Conflicting record or state change |
| `413` | Request body exceeds 256 KiB |
| `422` | Invalid request fields or arguments |
| `429` | Request rate limit reached |
| `503` | Provider, database or password-processing capacity is unavailable |

These are HTTP API errors. A valid MCP `tools/call` request can instead return a tool result marked `isError`; clients must inspect the result as well as the HTTP status. Retrying a failed write is unsafe until its store outcome is reconciled.

## Identity and browser sessions

| Method and route | Request | Result |
| --- | --- | --- |
| `POST /api/auth/login` | `{email, password}` | `{user, csrf_token}` and a session cookie |
| `GET /api/auth/me` | None | `{user, csrf_token}` |
| `POST /api/auth/logout` | None | Revokes the current session and clears its cookie |
| `GET /api/invites/{token}` | None | `{email, role, expires_at}` |
| `POST /api/auth/accept-invite` | `{token, name, password}` | `{user, csrf_token}` and a fresh invited-account session |
| `GET /api/health` | None | `{status, chat_configured, store_url, gateway_url}` |

The session cookie is HttpOnly with SameSite=Lax. Authenticated browser mutations require `X-CSRF-Token`. Production deployments enable secure cookies. User summaries include `id`, `email`, `name`, `role`, `created_at` and `active`.

`GET /api/health` is public configuration information. `chat_configured: true` means a provider key is present; it does not verify that the key or its quota is usable.

Successful login and invitation acceptance revoke only the browser session they replace. Invalid or used invitations leave the current session unchanged. Browser tabs refresh identity after an account change and clear the previous account's workspace state. Invitation acceptance requires a password of 12 to 256 characters; an invitation expires after two days.

## Server registry

All authenticated users can see non-secret connection summaries. Registry mutations require an administrator.

| Method and route | Behavior |
| --- | --- |
| `GET /api/servers` | Lists connection summaries and discovered tools |
| `POST /api/servers` | Creates a connection |
| `PATCH /api/servers/{id}` | Updates editable connection fields |
| `DELETE /api/servers/{id}` | Deletes a connection |
| `POST /api/servers/{id}/refresh` | Refreshes discovery |
| `PATCH /api/servers/{id}/tools/permissions` | Updates per-tool permissions |

A create request uses:

```json
{
  "name": "Example service",
  "slug": "example",
  "url": "https://mcp.example.com/mcp",
  "auth_type": "none",
  "enabled": true
}
```

`auth_type` accepts `none`, `bearer` or `header`. Supply `credential` for authenticated servers and `header_name` for custom-header authentication. The slug is immutable after creation and must match `^[a-z][a-z0-9-]{0,23}$`. A blank or omitted credential on update preserves the current value; `clear_credential: true` removes it. Public upstream URLs require HTTPS; only exact configured internal service hosts can use HTTP.

Connection summaries include `id`, `name`, `slug`, `url`, `enabled`, `status`, `auth_type`, `header_name`, `has_credential`, `last_error`, `last_checked_at` and `tools`. Each tool includes `name`, `description`, `inputSchema`, and `permission`. Credential values are excluded.

Tool permissions are `read` or `write`. New tools default to administrator-only. To update permissions, send original upstream names:

```json
{
  "permissions": {
    "list_products": "read",
    "create_product": "write"
  }
}
```

## API keys and team

| Method and route | Request or result | Access |
| --- | --- | --- |
| `GET /api/keys` | Lists key records | Own keys; administrators see all |
| `POST /api/keys` | `{name, user_id?}` returns `{key, record}` | Members create their own; administrators can select another active user |
| `DELETE /api/keys/{id}` | Revokes the key | Owner or administrator |
| `GET /api/members` | Lists user summaries | Administrator |
| `PATCH /api/members/{id}` | `{role?, active?}` | Administrator |
| `GET /api/invites` | Lists `{id, email, role, expires_at, accepted_at}` | Administrator |
| `POST /api/invites` | `{email, role}` returns invitation details and `invite_url` | Administrator |

Key records contain `id`, `name`, `prefix`, `user_id`, `created_at`, `last_used_at` and `revoked_at`. Only creation returns the full key. Newly issued keys use the `harbor_` prefix; existing `cc_` keys remain valid until revoked. The final active administrator cannot be demoted or deactivated. Invitation links must be copied and shared privately; there is no email provider.

## Activity and chat

`GET /api/audit` returns `{items, total}` and accepts these query parameters:

| Parameter | Meaning |
| --- | --- |
| `limit` | Page size, default 50; allowed range 1 to 200 |
| `offset` | Non-negative offset, default 0 |
| `outcome` | Exact outcome, such as `success`, `error`, `denied` or `pending` |
| `server_id` | Upstream filter |
| `user_id` | User filter for administrators |

Members always see only their own records. Entries include the originating user, server and tool names, timestamp, duration, outcome, error and request ID.

`POST /api/chat` accepts:

```json
{
  "messages": [
    {"role": "user", "content": "What products are in the store?"}
  ]
}
```

Roles are `user` or `assistant`, and the last message must be from the user. A request accepts up to 30 messages, 16,000 characters per message and 40,000 characters in total.

The response contains `message` and `tool_calls`. Tool calls include `name`, `arguments`, `result`, `is_error` and `duration_ms`. A write proposal also returns `confirmation` with `id`, `summary`, `tool_name` and `arguments`.

Send `POST /api/chat/confirm` with `{id, approve}` to approve or cancel a proposal. The server binds it to the session and user, enforces its five-minute expiry and one-use limit, and rechecks the reviewed target and current authority. Actual tool calls use the MCP HTTP gateway with a session-derived credential. These confirmations govern dashboard chat; an external administrator client can call permitted write tools directly.

Responses are not streamed. Missing Gemini configuration produces a `503` error. Chat history is supplied by the browser on each request, not stored as a server-side conversation.

## Service interfaces

| Service | Port | Entry point | Health routes |
| --- | --- | --- | --- |
| Gateway | 8000 | `uvicorn backend.app:app --host 0.0.0.0 --port 8000` | `/health/live`, `/health/ready` |
| MCP upstream | 8001 | `python -m upstreams.server` | `/health/live`, `/health/ready` |

Each upstream serves MCP at `/mcp`. `UPSTREAM_KIND` selects `woo`, `policies` or `shipping`, and `UPSTREAM_TOKEN` is required. Commerce also needs `WOO_URL`, `WOO_KEY` and `WOO_SECRET`. Set `WOO_PUBLIC_URL` to the store's public origin for OAuth signing when it differs from the internal transport URL. `STORE_TIMEZONE` defaults to `Asia/Kolkata`.

Gateway readiness checks PostgreSQL connectivity. Upstream readiness checks the MCP process itself, not the downstream store, so a WooCommerce outage is reported by tool calls without restarting every dependent service.

Tool-call guards use `X-Harbor-Read-Only` and `X-Harbor-Target`. For existing clients, `X-Commerce-Read-Only` remains accepted: either read-only header set to `true` restricts writes. `X-Commerce-Target` is used only when the Harbor target header is absent; conflicting supplied targets are denied and audited. New clients emit only the Harbor headers.

The Helm deployment adds trusted proxy handling to the gateway command. `FORWARDED_ALLOW_IPS` comes from the chart's narrowly configured ingress proxy CIDRs. Do not trust arbitrary forwarding headers; see [operations](operations.md).

## Gateway configuration

Configuration is read from environment variables. The chart provides deployed values and Secret references.

| Variable | Purpose or default |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy connection URL using `postgresql+psycopg://` |
| `SESSION_SECRET` | Secret used for session-related signatures |
| `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`, `BOOTSTRAP_ADMIN_NAME` | Initial administrator identity |
| `GEMINI_API_KEY` | Optional; chat is unavailable without it |
| `GEMINI_MODEL` | Defaults to `gemini-3.1-flash-lite` |
| `GEMINI_REQUEST_TIMEOUT_SECONDS` | Provider deadline, default 45 seconds |
| `CHAT_TIMEOUT_SECONDS` | Chat deadline, default 120 seconds |
| `STORE_URL` | Browser-facing store URL |
| `GATEWAY_URL` | Browser/client-facing gateway URL, including `/mcp` |
| `INTERNAL_GATEWAY_URL` | Defaults to `http://127.0.0.1:8000/mcp` |
| `ALLOWED_ORIGINS` | Comma-separated allowed browser origins |
| `ALLOWED_INTERNAL_HOSTS` | Comma-separated exact service DNS names trusted as internal upstreams |
| `SECRET_BACKEND` | `kubernetes` for deployment; `file` for local development only |
| `SECRET_FILE` | Local file-mode path, default `.secrets/upstreams.json` |
| `UPSTREAM_CREDENTIAL_SECRET` | Mutable credential Secret, default `harbor-upstream-credentials` |
| `POD_NAMESPACE` | Kubernetes namespace, default `harbor` |
| `BOOTSTRAP_SERVERS_JSON` | Initial connections and their reviewed read tools |
| `TOOL_TIMEOUT_SECONDS` | Upstream call deadline, default 15 seconds |
| `DISCOVERY_TIMEOUT_SECONDS` | Discovery deadline, default 8 seconds |
| `HEALTH_INTERVAL_SECONDS` | Background health interval, default 15 seconds |
| `SESSION_HOURS` | Session lifetime, default 12 hours |
| `SECURE_COOKIES` | Enable for public HTTPS deployment; local default is false |

`BOOTSTRAP_SERVERS_JSON` contains objects with `name`, `slug`, `url`, `credential_key` and `read_tools`. Gateway credentials are stored in the mutable Kubernetes Secret keyed by slug. Upstream pods use separate fixed service-token fields in the application Secret. Bootstrap records completion in PostgreSQL and never recreates connections subsequently deleted by an administrator.

## Deployment contract

The local ingress serves `http://harbor.localhost:8080` and `http://store.localhost:8080`. The MCP endpoint uses `/mcp` on the console host. Host port bindings are restricted to loopback.

Local images are `harbor:dev` for the backend and upstreams, `harbor-dashboard:dev` for the frontend, and `harbor-wordpress:dev` for the store. Services are ClusterIP. Only dashboard, gateway and store traffic passes through ingress. The chart includes resource requests/limits, health probes, persistent volumes and NetworkPolicies.

Scripts generate credentials; source and Helm values contain no deployed secrets. Use the [operations guide](operations.md) for complete installation, production configuration, backup and recovery instructions.
