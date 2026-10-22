# External MCP clients

Connect a compatible MCP client to Harbor to use the same tools as console chat under your own identity. You need the gateway URL ending in `/mcp` and a personal API key. You do not need WooCommerce keys or upstream credentials.

Use **Streamable HTTP** as the transport. The client must support a bearer `Authorization` header. Harbor does not provide an MCP OAuth browser-login flow or a locally launched stdio endpoint.

## Connect a client

1. Sign in to the Harbor instance you want to use, preferably as a member for read-only access.
2. In **My API keys** (or **Team & keys** for administrators), create a named key and copy it while it is displayed.
3. Configure the client with URL `http://harbor.localhost:8080/mcp` and header `Authorization: Bearer <MEMBER_GATEWAY_KEY>`, replacing the placeholder with your key.
4. Connect and list tools. The default setup exposes seven member tools across three upstreams. Tool names include a connection identifier and a stable suffix, so identically named tools remain distinct.
5. Call the discovered `list_products` tool using its complete returned name, then check **Activity** for the call under your identity.

The local URL works only from the computer running the cluster. For the hosted deployment, replace it with `https://harbor.satwiksps.site/mcp` in the examples below and use a key created in that deployment. The documentation site is not an MCP endpoint.

After a hostname change, update the client's configured URL directly. Existing gateway keys remain valid, but clients may drop Authorization headers when following redirects to another hostname.

Use a member key for read-only clients. An administrator key can invoke write tools directly; dashboard chat confirmations do not apply to external calls.

## MCP Inspector

MCP Inspector can list and call tools without a model provider or access to Harbor's source. With Node.js 24 installed, open PowerShell in a private working directory and install Inspector 2.11.0:

```powershell
npm.cmd install --prefix .tools/external-client --no-audit --no-fund @modelcontextprotocol/inspector@2.11.0
New-Item -ItemType Directory -Force .secrets | Out-Null
notepad .secrets/inspector.json
```

Save the following configuration in `.secrets/inspector.json`, replacing the key placeholder and gateway URL. Keep it private. Harbor's repository excludes `.secrets` from Git; in another directory, arrange equivalent exclusion before using version control. Inspector 2.11 uses `type: "http"` for Streamable HTTP:

```json
{
  "mcpServers": {
    "harbor": {
      "type": "http",
      "url": "http://harbor.localhost:8080/mcp",
      "protocolEra": "auto",
      "headers": {"Authorization": "Bearer <MEMBER_GATEWAY_KEY>"},
      "connectionTimeout": 15000,
      "requestTimeout": 25000
    }
  }
}
```

List the available tools:

```powershell
node --dns-result-order=ipv4first `
  .tools/external-client/node_modules/@modelcontextprotocol/inspector/clients/launcher/build/index.js `
  --cli --config .secrets/inspector.json --server harbor `
  --format json --quiet --stored-auth-only --method tools/list
```

The response includes each tool's complete `name` and `inputSchema`. Find the tool whose name contains `list_products`, copy that complete name, then call it with a small page size:

```powershell
$toolName = Read-Host 'Complete list_products tool name'
node --dns-result-order=ipv4first `
  .tools/external-client/node_modules/@modelcontextprotocol/inspector/clients/launcher/build/index.js `
  --cli --config .secrets/inspector.json --server harbor `
  --format json --quiet --stored-auth-only --method tools/call `
  --tool-name $toolName --tool-args-json '{"per_page":3}'
```

Check the result's `isError` field as well as the command's exit status: a valid MCP request can return a tool error. The call also appears in Harbor's **Activity** page.

The Node flag prefers IPv4 because local ingress binds to `127.0.0.1`. `--stored-auth-only` prevents Inspector from starting an interactive OAuth flow. Keep the key in the private configuration file rather than putting it in command-line arguments.

After disabling a connection or changing permissions, refresh the catalog or reconnect. Stale tool names are rejected even if a client caches them. Revocation takes effect on the next request, including an initialized transport; a call already dispatched may finish.

## Ask a question with the reference agent

The reference agent requires source access. The repository includes `examples/ask_gateway.py`, a standalone client using the official MCP and Google Gen AI SDKs. It discovers tools over HTTP without importing Harbor's backend. The model can use only those tools, with no shell, filesystem, browser or direct upstream access.

Run `uv sync --frozen` first. Use the member's gateway key and your Gemini provider key. They serve different purposes: the gateway key identifies your Harbor account; the provider key authorizes the model request.

These PowerShell commands keep the gateway secret out of command history and clear credential environment variables even if the agent fails:

```powershell
$env:GATEWAY_URL = 'http://harbor.localhost:8080/mcp'
try {
    $env:GATEWAY_API_KEY = Read-Host -MaskInput 'Member gateway key'
    $env:GEMINI_API_KEY = (Get-Content -Raw .secrets/gemini-api-key.txt).Trim()
    uv run python examples/ask_gateway.py --prompt "Read today's orders and the shipping policy. Which orders qualify for free shipping? Use both upstreams and cite actual order IDs and policy version."
} finally {
    Remove-Item Env:GATEWAY_API_KEY, Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
}
```

If there are no orders today, an empty result is expected. Use an actual order ID in a follow-up prompt if you want to inspect an older order.

The default model is `gemini-3.1-flash-lite`. To use another compatible model, pass `--model MODEL_NAME`. Quota and availability depend on your account; check [Google's pricing and limits](https://ai.google.dev/gemini-api/docs/pricing). The agent does not automatically switch models or retry provider failures.

### Agent limits

The agent accepts up to 40 read-only tools and 48 KiB of function declarations. It follows MCP pagination and rejects incomplete or malformed catalogs. Larger catalogs fail before a model request; use console chat's [bounded tool discovery](system-design.md#large-tool-catalogs-in-chat) or a client with its own tool-selection strategy.

An agent run has a 120-second deadline, 45 seconds per model request, at most six model rounds and eight tool calls. Only explicitly read-only tools are offered to the model. Requests include `X-Harbor-Read-Only: true`, so the gateway also rejects a tool reclassified as a write during the run, even if the key belongs to an administrator.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| Connection refused | The instance is running, the URL ends in `/mcp`, and a local URL is being used on the cluster's computer |
| Unauthorized | The full bearer key is present, belongs to this deployment and has not been revoked |
| A tool is missing | Your role permits it and its connection is enabled; refresh the client catalog after changes |
| A cached tool name fails | List tools again and use the complete current name |
| The agent cannot access Gemini | The provider key is set, the configured model is available and the account has quota |
| The agent rejects a large catalog | Use console chat or a client with bounded tool selection |

For automated integration checks, see [External client verification](development.md#external-client-verification). For protocol details, see the [Inspector CLI documentation](https://github.com/modelcontextprotocol/inspector/blob/main/clients/cli/README.md) and [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
