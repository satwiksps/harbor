# External MCP clients

An external client needs one MCP URL ending in `/mcp` and a member API key. It never receives WooCommerce keys or private upstream credentials.

Verification uses MCP Inspector 2.11.0 as the named third-party protocol client and a standalone external agent at `examples/ask_gateway.py` in the private application repository for the natural-language requirement. The agent uses the official MCP and Google Gen AI SDKs. It imports no backend code, discovers its tools over HTTP, and authenticates every gateway request with the member key. Its model has no shell, filesystem, browser, or direct upstream tools.

## Run the acceptance harness

Install Python dependencies with `uv sync`, then install the pinned Inspector with Node.js 22.19 or newer:

```powershell
npm.cmd install --prefix .tools/external-client --no-audit --no-fund @modelcontextprotocol/inspector@2.11.0
uv run python -m tests.external_agent --prepare-only
```

Deploy the cluster and run `uv run python -m tests.acceptance` first. That creates the second user's credentials in ignored `.secrets/demo-member.json`. The harness reads the chosen provider key from `.secrets/gemini-api-key.txt` without printing it.

```powershell
uv run python -m tests.external_agent
```

Add `--order-id 25` when order 25 exists to include its actual destination and subtotal in a shipping quote. Substitute an order from your store on a fresh installation. The verified local run used this option and successfully called WooCommerce, Store policies and Shipping as the member.

The harness signs in as the member and generates a temporary member API key. Inspector must discover tools from all three upstreams and call today's orders. Then the standalone agent receives one prompt asking for today's WooCommerce orders and the shipping policy. The harness requires successful gateway audit entries from two upstreams, both attributed to that member. It finally revokes the temporary key and signs out, including after failure.

The redacted report at `.runtime/external-agent-report.json` contains tool names, the actual model answer, client versions, member attribution, audit evidence, and cleanup status. An answer that merely claims to have used tools does not pass. `.tools`, `.secrets`, and `.runtime` are ignored by Git.

The [public evidence extract](assets/external-agent-evidence.json) contains the verified checks, tool names and factual order answer, without credentials, user IDs or session data.

To verify the named MCP client without a model request:

```powershell
uv run python -m tests.external_agent --inspector-only
```

This mode explicitly records that the natural-language agent was not attempted.

## Run the independent agent yourself

Use the member's gateway key from Team & keys. These PowerShell commands avoid putting the secret in command history:

```powershell
$env:GATEWAY_URL = 'http://console.localhost:8080/mcp'
$env:GATEWAY_API_KEY = Read-Host -MaskInput 'Member gateway key'
$env:GEMINI_API_KEY = (Get-Content -Raw .secrets/gemini-api-key.txt).Trim()
uv run python examples/ask_gateway.py --prompt "Read today's orders and the shipping policy. Which orders qualify for free shipping? Use both upstreams and cite actual order IDs and policy version."
Remove-Item Env:GATEWAY_API_KEY, Env:GEMINI_API_KEY
```

The exact model is `gemini-3.1-flash-lite`, with low thinking and no automatic model substitution. Google lists [free input and output for its standard API tier](https://ai.google.dev/gemini-api/docs/pricing). Quota and availability depend on the account. The agent reports quota or provider failures honestly, never rotates keys, and never enables billing.

The process has a 120-second agent deadline, 45 seconds per model request, at most six rounds and eight tool calls. Provider retries are disabled. Only explicitly read-only discovered tools are offered to the model. Every request also sends `X-Commerce-Read-Only: true`, so the gateway rejects a tool reclassified as a write while the model was thinking, even if the supplied key belongs to an administrator. The gateway independently enforces current user permissions on every call. The acceptance subprocess has an additional 150-second bound and terminates descendants on timeout.

## MCP Inspector configuration

Inspector 2.11 uses `type: "http"` for Streamable HTTP:

```json
{
  "mcpServers": {
    "commerce": {
      "type": "http",
      "url": "http://console.localhost:8080/mcp",
      "protocolEra": "auto",
      "headers": {"Authorization": "Bearer <MEMBER_GATEWAY_KEY>"},
      "connectionTimeout": 15000,
      "requestTimeout": 25000
    }
  }
}
```

Keep real-key configurations under `.secrets` and use Inspector's `--config` option. Authorization headers must not appear in command-line arguments, screenshots, or recordings. The harness creates this file briefly and removes it afterward. In the visual Inspector, select Streamable HTTP, enter the gateway URL and bearer header, then list and call tools.

After disabling an upstream, refresh the client's tool catalog or reconnect. A stale tool name is rejected immediately even if the client has cached its catalog. Key revocation applies to the next request, including an initialized transport.

## Optional Gemini CLI troubleshooting

Gemini CLI 0.64.0 was tested on this Windows environment but did not pass the multi-upstream prompt. MCP connectivity worked; its configuration handling, policy filtering and automatic model promotion caused unreliable tool execution. A separate `mcp list` diagnostic also hit a Node.js shutdown assertion. It is not a required dependency or a claimed passing client. The independent SDK agent avoids those client-specific behaviors while making real model and MCP requests.

References: [Inspector CLI](https://github.com/modelcontextprotocol/inspector/blob/main/clients/cli/README.md), [Inspector configuration](https://github.com/modelcontextprotocol/inspector/blob/main/docs/mcp-server-configuration.md), [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [Gemini function calling](https://ai.google.dev/gemini-api/docs/function-calling).
