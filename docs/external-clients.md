# External MCP clients

An external client needs one MCP URL ending in `/mcp` and a member API key. It never receives WooCommerce keys or private upstream credentials.

Use **Streamable HTTP** as the transport. A client must support a bearer Authorization header; a configuration intended only for a locally launched stdio server will not work with this endpoint.

## Connect a client

1. Start Harbor and sign in as a member.
2. In **My API keys**, create a named key and copy it while it is displayed.
3. Configure the client with URL `http://harbor.localhost:8080/mcp` and header `Authorization: Bearer <MEMBER_GATEWAY_KEY>`.
4. Connect and list tools. The bundled setup exposes seven member tools across three upstreams. Names include the upstream slug and a stable suffix, so the two tools named `search` remain distinct.
5. Call the discovered `list_products` tool using its complete returned name, then check **Activity** for the call under your member identity.

The local URL works only from the computer running the cluster. For the hosted deployment, replace it with `https://harbor.satwiksps.site/mcp` in the examples below and use a key created in that deployment. The documentation site is not an MCP endpoint.

After a hostname change, update the client's configured URL directly. Existing gateway keys remain valid, but clients may drop Authorization headers when following redirects to another hostname.

Use a member key for read-only clients. An administrator key can invoke write tools directly; dashboard chat confirmations do not apply to external calls.

## MCP Inspector

The verified protocol client is MCP Inspector 2.11.0. With Node.js 24 installed, add it to the ignored local tools directory:

```powershell
npm.cmd install --prefix .tools/external-client --no-audit --no-fund @modelcontextprotocol/inspector@2.11.0
```

Inspector 2.11 uses `type: "http"` for Streamable HTTP. Save a configuration under `.secrets`, replacing the placeholder in your editor:

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

If the file is `.secrets/inspector.json`, list the available tools:

```powershell
node --dns-result-order=ipv4first `
  .tools/external-client/node_modules/@modelcontextprotocol/inspector/clients/launcher/build/index.js `
  --cli --config .secrets/inspector.json --server harbor --method tools/list
```

The Node flag prefers IPv4 because the local ingress binds to `127.0.0.1`. Never put the real Authorization header in command-line arguments, screenshots or recordings. In the visual Inspector, use the same transport, URL and header, then list and call tools.

After disabling a connection or changing permissions, refresh the catalog or reconnect. Stale tool names are rejected even if a client caches them. Revocation takes effect on the next request, including an initialized transport; a call already dispatched may finish.

## Ask a question with the reference agent

The private repository includes `examples/ask_gateway.py`, a standalone client using the official MCP and Google Gen AI SDKs. It imports no backend code and discovers tools over HTTP. The model can use only those tools, with no shell, filesystem, browser or direct upstream access.

The client follows MCP pagination instead of stopping after the first 100 tools. Its simple model adapter accepts at most 40 read-only tools and 48 KiB of complete function declarations. Larger catalogs produce a clear error before any model request; use Harbor chat's [bounded tool discovery](system-design.md#large-tool-catalogs-in-chat) or another client with its own selection strategy. Discovery itself stops on repeated cursors, duplicate names, more than 100 pages or more than 4 MiB of catalog data.

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

The default model is `gemini-3.1-flash-lite`, with low thinking and no automatic model substitution. Quota and availability depend on the account; check [Google's current pricing and limits](https://ai.google.dev/gemini-api/docs/pricing). The agent reports provider failures without rotating keys or enabling billing.

The process has a 120-second agent deadline, 45 seconds per model request, at most six rounds and eight tool calls. Provider retries are disabled. Only explicitly read-only discovered tools are offered to the model. Every request also sends `X-Harbor-Read-Only: true`, so the gateway rejects a tool reclassified as a write while the model was thinking, even if the supplied key belongs to an administrator. The gateway independently enforces current user permissions on every call. The acceptance subprocess has an additional 150-second bound and terminates descendants on timeout.

## Reproduce the integration check

These checks create synthetic accounts and temporary keys. Run them on the local demo with other clients idle. They are separate from connecting a normal client.

After deploying the cluster and installing Inspector:

```powershell
uv sync --frozen
uv run python -m tests.external_agent --prepare-only
uv run python -m tests.acceptance
uv run python -m tests.external_agent --inspector-only
```

The acceptance harness creates the demo member and saves its credentials in ignored `.secrets/demo-member.json`. `--prepare-only` checks the installed Inspector without API calls. `--inspector-only` verifies discovery and calls without making a model request.

For the complete natural-language check, save a provider key as described in [setup](getting-started.md#configure-optional-chat), then run:

```powershell
uv run python -m tests.external_agent
```

Optionally add `--order-id ID`, substituting a real numeric order ID from your store, to include its destination and subtotal in a shipping quote. The recorded run used order 25; that ID is not a prerequisite for a new installation.

Inspector must discover tools from all three upstreams and call today's orders. The agent then receives one prompt about orders and shipping policy. The harness requires successful audit entries from two upstreams, attributed to the member, and revokes its temporary key and signs out during cleanup. An answer that only claims to have used tools does not pass.

The private `.runtime/external-agent-report.json` records the model answer, client versions, checks and cleanup. The [public evidence extract](assets/external-agent-evidence.json) omits credentials, user IDs and sessions. The full verification history is in [Verification](verification.md).

Gemini CLI 0.64.0 did not pass the recorded multi-upstream prompt on Windows and is not a required or verified client. Use Inspector for protocol inspection and the reference agent for the documented model workflow.

References: [Inspector CLI](https://github.com/modelcontextprotocol/inspector/blob/main/clients/cli/README.md), [Inspector configuration](https://github.com/modelcontextprotocol/inspector/blob/main/docs/mcp-server-configuration.md), [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling).
