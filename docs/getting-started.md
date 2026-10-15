# Getting started

Run the complete console and storefront on a local Windows machine. Commands below run in PowerShell 7 from the repository root.

## Prerequisites

| Requirement | Purpose |
| --- | --- |
| Docker Desktop with Linux containers running | Hosts the dedicated k3d Kubernetes cluster |
| PowerShell 7 (`pwsh`), Git and `kubectl` | Runs setup and manages the cluster |
| Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/) | Installs the locked Python environment |
| At least 8 GB allocated to Docker | The project node is capped at 6 GB |
| Available ports 8080 and 8443 | Local ingress, bound to loopback |
| Internet access for initial setup | Downloads images, dependencies and fonts |
| Node.js 24, when developing the frontend or using MCP Inspector | Not required for the containerized dashboard build |

On a 16 GB computer, run image builds and browser verification sequentially and leave sufficient free host memory. The bootstrap script installs the verified Helm and k3d versions under `.tools`.

The application repository is private. Obtain access, clone it and open PowerShell in the checkout before continuing.

## Configure optional chat

Install the locked Python environment:

```powershell
uv sync --frozen
```

For chat, save only your Gemini API key in the ignored local key file:

```powershell
New-Item -ItemType Directory -Force .secrets | Out-Null
notepad .secrets/gemini-api-key.txt
```

Save and close the editor. Do not commit or share this file. The configured model is `gemini-3.1-flash-lite`. Use a free-tier project if available for your account and check [Google's current pricing and quota](https://ai.google.dev/gemini-api/docs/pricing) before sending requests. The application does not enable billing or switch keys when quota runs out.

You can skip the key to use management and MCP without chat. Chat reports that the provider is unavailable instead of returning a simulated answer. To configure it later, create the file and rerun bootstrap.

## Start the system

```powershell
pwsh -File scripts/bootstrap.ps1
```

Initial setup takes several minutes. The script:

1. Verifies the local tools and starts the dedicated `urumi-commerce` cluster.
2. Generates random credentials in `.secrets/development.json` and creates Kubernetes Secrets.
3. Builds and imports the application, dashboard and WordPress images.
4. Installs the Helm chart and initializes the store and gateway.

Rerunning bootstrap preserves existing store and gateway data. It does not replace connection credentials that administrators have changed. Keep `.secrets/development.json`: editing or replacing it does not rotate passwords already stored in the databases. See [operations](operations.md) for recovery procedures.

## Sign in

| Service | Local address |
| --- | --- |
| Console | [console.localhost:8080](http://console.localhost:8080) |
| Storefront | [store.localhost:8080](http://store.localhost:8080) |
| WordPress administration | [store.localhost:8080/wp-admin/](http://store.localhost:8080/wp-admin/) |
| MCP endpoint | `http://console.localhost:8080/mcp` |

The `.localhost` names resolve to loopback. No Windows hosts-file change is needed. These addresses refer to the machine running the cluster, not a hosted demonstration.

Open `.secrets/development.json` in your editor to find `admin_email`, `admin_password`, `wordpress_admin_user` and `wordpress_admin_password`. Bootstrap restricts the directory to your Windows account and never prints passwords. Keep the file and login details private.

Sign in to the console as administrator, then open **Connections**. The three bundled servers should be connected and display their discovered tools. Continue with the [user guide](user-guide.md) to create a product, check out and invite a member.

## Stop and resume

Stop the project cluster while retaining its data:

```powershell
pwsh -File scripts/stop.ps1
```

Run bootstrap again to resume. Deliberate removal is documented in `scripts/uninstall.ps1`; do not delete persistent volumes if you intend to keep orders. For startup failures, retained credentials or local port recovery, use the [operations guide](operations.md).
