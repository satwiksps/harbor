# Getting started

Run Harbor and its storefront on a local Windows machine. Commands below run in PowerShell 7 from the repository root.

The result is a private local dashboard, a demo store and one MCP endpoint. The documentation website is not a hosted application.

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

## Prepare the checkout

Check that the required commands are available:

```powershell
pwsh --version
git --version
uv --version
kubectl version --client
docker info --format '{{.ServerVersion}}'
```

If the last command cannot reach Docker, start Docker Desktop and wait for its Linux engine before continuing.

Install the locked Python environment:

```powershell
uv sync --frozen
```

## Configure optional chat

For chat, save only your Gemini API key in the ignored local key file:

```powershell
New-Item -ItemType Directory -Force .secrets | Out-Null
notepad .secrets/gemini-api-key.txt
```

Save and close the editor. Do not commit or share this file. The configured model is `gemini-3.1-flash-lite`. Use a free-tier project if available for your account and check [Google's current pricing and quota](https://ai.google.dev/gemini-api/docs/pricing) before sending requests. The application does not enable billing or switch keys when quota runs out.

You can skip the key to use management and MCP without chat. Chat reports that the provider is unavailable instead of returning a simulated answer. To configure it later, create the file and rerun bootstrap.

## Start the system

For a retained installation that uses earlier resource names, first follow [the upgrade preservation steps](operations.md#preserve-an-existing-installation). Fresh installations use the Harbor defaults.

```powershell
pwsh -File scripts/bootstrap.ps1
```

Initial setup takes several minutes. The script:

1. Verifies the local tools and starts the dedicated project cluster.
2. Generates random credentials in `.secrets/development.json` and creates Kubernetes Secrets.
3. Builds and imports the application, dashboard and WordPress images.
4. Installs the Helm chart and initializes the store and gateway.

Rerunning bootstrap preserves existing store and gateway data. It does not replace connection credentials that administrators have changed. Keep `.secrets/development.json`: editing or replacing it does not rotate passwords already stored in the databases. See [operations](operations.md) for recovery procedures.

## Sign in

| Service | Local address |
| --- | --- |
| Harbor | [harbor.localhost:8080](http://harbor.localhost:8080) |
| Storefront | [store.localhost:8080](http://store.localhost:8080) |
| WordPress administration | [store.localhost:8080/wp-admin/](http://store.localhost:8080/wp-admin/) |
| MCP endpoint | `http://harbor.localhost:8080/mcp` |

The `.localhost` names resolve to loopback. No Windows hosts-file change is needed. These addresses refer to the machine running the cluster, not a hosted demonstration.

Open `.secrets/development.json` in your editor to find `admin_email`, `admin_password`, `wordpress_admin_user` and `wordpress_admin_password`. Bootstrap restricts the directory to your Windows account and never prints passwords. Keep the file and login details private.

Harbor and WordPress have separate accounts. The Harbor administrator credentials do not sign you into WordPress.

## Check the first run

1. Sign in to Harbor with `admin_email` and `admin_password`.
2. Open **Connections**. WooCommerce, Store policies and Shipping should show **Connected**, with nine tools in total.
3. Open the storefront. The initial catalog should contain three starter products.
4. If chat is configured, ask `What products are in the store?` and expand the returned tool result. Without a provider key, the chat notice is expected.
5. Open **Activity** after a tool call and check that it is attributed to your account.

A Connected status confirms MCP discovery, not a successful WooCommerce request or working Gemini quota. The first read checks that additional path. Continue with the [user guide](user-guide.md) to create a product, check out and invite a member.

If the page does not load, first check Docker and the bootstrap output. If a connection is Unreachable, open its error details. For a provider quota error, wait for quota to become available; changing gateway permissions will not fix it. The [troubleshooting guide](operations.md#troubleshooting) covers cluster and dependency failures.

## Stop and resume

Stop the project cluster while retaining its data:

```powershell
pwsh -File scripts/stop.ps1
```

Run `pwsh -File scripts/bootstrap.ps1 -SkipBuild` to resume the same checkout with its existing images. After code changes, omit `-SkipBuild` so those changes are built. Deliberate removal is documented in `scripts/uninstall.ps1`; do not delete persistent volumes if you intend to keep orders. For retained credentials or local port recovery, use the [operations guide](operations.md).
