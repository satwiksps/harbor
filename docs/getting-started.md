# Installation

Run the complete Harbor system on a Windows machine with Docker Desktop. Commands below run in PowerShell 7 from the repository root. For a Linux server deployment, use [Deployment and recovery](operations.md#vps-configuration).

You will get a local console, a sample WooCommerce store and one MCP endpoint. You need access to the private application repository. If you already have an account on a running Harbor instance, go straight to [Using the console](user-guide.md).

## Prerequisites

| Requirement | Purpose |
| --- | --- |
| Docker Desktop with Linux containers running | Hosts the dedicated k3d Kubernetes cluster |
| PowerShell 7 (`pwsh`), Git and `kubectl` | Runs setup and manages the cluster |
| Python 3.12 and [uv](https://docs.astral.sh/uv/getting-started/installation/) | Installs the locked Python environment |
| At least 8 GB allocated to Docker | The project node is capped at 6 GB |
| Available ports 8080 and 8443 | Local ingress, bound to loopback |
| Internet access for initial setup | Downloads images and dependencies |
| Node.js 24, when developing the frontend or using MCP Inspector | Not required for the containerized dashboard build |

Leave enough free host memory for Windows and Docker in addition to the 6 GB cluster. Run image builds and browser tests sequentially on memory-constrained machines. The bootstrap script installs pinned Helm and k3d versions under `.tools`; you do not need to install them globally.

Clone the repository and open PowerShell in the checkout before continuing.

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

Save and close the editor. Do not commit or share this file. The configured model is `gemini-3.1-flash-lite`. Provider access, pricing and quota depend on your Google account; check [Google's pricing and quota](https://ai.google.dev/gemini-api/docs/pricing) before sending requests.

You can skip the key to use management and MCP without chat. Chat reports that the provider is unavailable instead of returning a simulated answer. To configure it later, create the file and rerun bootstrap.

## Start the system

For a new installation, run:

```powershell
pwsh -File scripts/bootstrap.ps1
```

Initial setup takes several minutes. The script:

1. Verifies the local tools and starts the dedicated `harbor` cluster.
2. Generates random credentials in `.secrets/development.json` and creates Kubernetes Secrets.
3. Builds and imports the application, dashboard and WordPress images.
4. Installs the Helm chart and initializes the store and gateway.

Bootstrap switches the current Kubernetes context to `k3d-harbor`. It does not remove other Docker resources.

Rerunning bootstrap preserves existing store and gateway data. It does not replace connection credentials that administrators have changed. Keep `.secrets/development.json`: editing or replacing it does not rotate passwords already stored in the databases. To resume or upgrade an existing installation, retain its credentials and follow [Deployment and recovery](operations.md).

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
3. Open the storefront. A fresh installation contains three starter products.
4. If chat is configured, ask `What products are in the store?` and expand the returned tool result. Without a provider key, the chat notice is expected.
5. Open **Activity** after a tool call and check that it is attributed to your account.

A **Connected** status means Harbor can discover the service's tools. It does not verify a WooCommerce data request or Gemini quota. The first chat read checks both paths. Without chat, use [MCP Inspector](external-clients.md#mcp-inspector) to make a read call without a model provider. Continue with [Using the console](user-guide.md) to manage access and work with the store.

If the page does not load, first check Docker and the bootstrap output. If a connection is Unreachable, open its error details. For a provider quota error, wait for quota to become available; changing gateway permissions will not fix it. The [troubleshooting guide](operations.md#troubleshooting) covers cluster and dependency failures.

## Stop and resume

Stop the project cluster while retaining its data:

```powershell
pwsh -File scripts/stop.ps1
```

Resume the same checkout with its existing images:

```powershell
pwsh -File scripts/bootstrap.ps1 -SkipBuild
```

After code changes, omit `-SkipBuild` so the new images are built. For backups, removal or local port recovery, use [Deployment and recovery](operations.md).
