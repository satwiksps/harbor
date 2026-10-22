# Operations and deployment

Harbor runs on one Kubernetes node, with one replica of each service. Local development uses k3d and Docker Desktop; a Linux VPS uses K3s and the same Helm chart. Persistent volumes survive pod replacement, but local-path storage does not protect against node loss. Keep verified backups off the node.

Use [Getting started](getting-started.md) for a first installation. This guide covers maintenance, production configuration and recovery. Commands assume the fresh-install namespace and release `harbor`; retained installations must use their recorded names.

## Local lifecycle

Use PowerShell 7 from the repository root. Docker Desktop must be running Linux containers.

```powershell
./scripts/install-tools.ps1
./scripts/bootstrap.ps1
```

Helm 4.3.1 and k3d 5.9.0 are installed only in `.tools`, with SHA256 checks against official release files. Bootstrap selects the dedicated project cluster's kubectl context. Fresh installations use cluster `harbor`, context `k3d-harbor`, and namespace and release `harbor`. The server uses the tested `rancher/k3s:v1.35.5-k3s1` image and a 6 GiB memory cap. Existing Docker containers and clusters are not removed.

### Preserve an existing installation

For an existing installation with different resource names, record its actual `clusterName`, `namespace`, `releaseName`, `postgresUser` and `postgresDatabase` in the ignored `.runtime/local-settings.json` before running setup or maintenance. The scripts read these overrides; they do not discover an earlier installation automatically. Keep the existing cluster, namespace, Helm release, database identity and persistent volumes so the upgrade uses the same data.

The examples below use fresh-install defaults. Substitute the recorded namespace, release and PostgreSQL names in manual commands when operating a retained installation. `backup.ps1` includes `local-settings.json` when it exists; restore it to `.runtime` before invoking maintenance on recovered data.

### Select the local context

The commands in this guide use the selected kubectl context. Before running them, inspect the available contexts and select the one created by bootstrap. Do not use a production or unrelated project context.

```powershell
kubectl config get-contexts
kubectl config current-context
$projectContext = Read-Host 'Context created by the project bootstrap'
kubectl config use-context $projectContext
kubectl -n harbor get deployments
```

For the Docker diagnostics later in this guide, list k3d nodes and record the actual server and load-balancer names belonging to that same project cluster. Do not rename the cluster or infer names from the examples.

```powershell
.tools/k3d.exe node list
$projectServer = Read-Host 'Project server container name from the list'
$projectLoadBalancer = Read-Host 'Project load-balancer container name from the list'
```

Keep those variables in the same PowerShell session. Check the cluster and role columns before selecting the names.

### Credentials and local access

`bootstrap.ps1` creates random credentials in the access-restricted, ignored `.secrets/development.json`, creates Kubernetes Secrets, builds and imports three images, and installs the chart. Put a Gemini key in `.secrets/gemini-api-key.txt` first, or configure it later and rerun bootstrap. No script prints passwords. Keep the generated credential file: changing it does not rotate credentials already stored in PostgreSQL or MariaDB.

Local names use the `.localhost` domain, so an administrator hosts-file edit is unnecessary. New clusters explicitly publish ingress ports 8080/8443 and the selected Kubernetes API port on `127.0.0.1`. The socket binding, rather than the hostname, limits access to this computer:

| Application | Address |
| --- | --- |
| Dashboard | `http://harbor.localhost:8080` |
| Gateway | `http://harbor.localhost:8080/mcp` |
| Store | `http://store.localhost:8080` |
| Store admin | `http://store.localhost:8080/wp-admin/` |

Only Traefik ingress and the Kubernetes administration API are published, both on loopback. The WordPress Service listens on port 80 and forwards to non-root Apache on 8080. Gateway, dashboard, upstreams, and databases are all ClusterIP Services. The upstream port is 8001 and has no Ingress route.

### Rebuild, stop and remove workloads

```powershell
# Rebuild and deploy after a source change.
./scripts/build-images.ps1
./scripts/deploy.ps1

# Stop the cluster while retaining all data.
./scripts/stop.ps1

# Resume with the existing built images.
./scripts/bootstrap.ps1 -SkipBuild

# Remove workloads but retain the namespace, generated Secrets and PVCs.
./scripts/uninstall.ps1
```

The deployment script creates `.runtime/values-runtime.yaml` with the actual node IP for API-server egress, its pod CIDRs for ingress proxy trust, and a new rollout revision. This makes a local `:dev` image rebuild restart the application pods. Production should use immutable version tags or digests instead.

### Store initialization and versions

The WordPress bootstrap Job runs after installation and upgrades. It installs WordPress once, activates the packaged WooCommerce and Storefront versions, and creates missing API credentials. Starter products and classic cart/checkout content are initialized only before the `harbor_seed_complete` marker is set; later product deletions and page edits remain intact. It configures INR, the Asia/Kolkata timezone, COD and the sample shipping table, and turns off WooCommerce coming-soon mode so anonymous visitors can browse. If no published privacy page exists, it publishes a short sample data-use notice once and links checkout to it; later edits remain intact. No outbound email provider is configured. The original product SVGs are packaged assets; arbitrary SVG uploads are not enabled. Products without media have a labeled typographic card rather than a broken-image icon.

Initialization runs through WP-CLI. Apache blocks the browser installer and setup-configuration endpoints, including path suffixes, from the first start. Normal administrator sign-in remains available after bootstrap.

The tested store image pins WordPress 7.1.3, PHP 8.3, WooCommerce 11.2.1, Storefront 4.6.2 and WP-CLI 2.12.0. Plugin and theme downloads have checked SHA256 values in the Dockerfile. The database images are MariaDB 11.4.13 and PostgreSQL 17.11. Version changes should go through backup and compatibility checks.

## VPS configuration

Use a dedicated Linux VM with enough capacity for the workloads in `deploy/chart/values.yaml`, persistent storage, a public address and two DNS names. The deployment helper supports a single K3s node. Choose CPU architecture, disk sizes and resource limits for your environment; cloud account and billing setup are outside Harbor.

The commands below use Ubuntu, x86-64, K3s `v1.35.5+k3s1`, Helm `4.3.1` and cert-manager `v1.21.2`. Use images and installer archives matching your host architecture.

### Prepare the public host

Use one VM and its existing public address, without an external cloud load balancer. In the cloud firewall allow TCP 80/443 from the internet and TCP 22 only from the operator's current public IP. Keep 6443, database ports and upstream ports closed to the internet. SSH provides the administration path. Inspect the host firewall too. Preserve the cloud image's storage, metadata and management rules while permitting web traffic and K3s pod forwarding; do not replace the ruleset blindly.

Create DNS records for separate console and store hosts, such as `console.example.com` and `store.example.com`, pointing to the VM. Confirm both names resolve before requesting a certificate. Documentation is a separate static site and does not need an ingress route in this cluster. Preserve any unrelated DNS records.

A future VM move can retain the same application names by updating their A records after restoring and testing the destination. Changing the application hostname also requires updating ingress hosts, public URLs and WordPress's stored URLs before checking redirects, sign-in, invitations, checkout and external clients.

On a fresh Ubuntu VM, install Docker only as an image builder, plus Python 3, curl and CA certificates. Keep k3s on its own containerd runtime. Docker can change the host's FORWARD policy, so inspect the resulting rules and verify external HTTP traffic after installation. Do not expose the Docker API.

Download and review the pinned k3s installer, then install with Secret encryption:

```sh
curl -fsSLo /tmp/install-k3s.sh 'https://raw.githubusercontent.com/k3s-io/k3s/v1.35.5%2Bk3s1/install.sh'
sudo env INSTALL_K3S_VERSION='v1.35.5+k3s1' sh /tmp/install-k3s.sh server \
  --secrets-encryption --write-kubeconfig-mode 600
sudo k3s secrets-encrypt status
sudo kubectl get nodes -o wide
```

Do not set `node-external-ip` for a node behind public-IP NAT when preserving client addresses with `externalTrafficPolicy: Local`. K3s documents that combination as incompatible. The provided Traefik configuration uses `Local` and leaves forwarding-header trust disabled. See [K3s ServiceLB behavior](https://docs.k3s.io/networking/networking-services#usage).

Install the pinned Helm archive matching the VM architecture and verify its official checksum. For AMD:

```sh
curl -fsSLO https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz
curl -fsSLO https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz.sha256sum
sha256sum -c helm-v4.3.1-linux-amd64.tar.gz.sha256sum
tar -xzf helm-v4.3.1-linux-amd64.tar.gz
sudo install -m 0755 linux-amd64/helm /usr/local/bin/helm
```

Install cert-manager once at cluster level. The chart signature is checked against the project's published keyring:

```sh
curl -fsSLo /tmp/cert-manager.gpg https://cert-manager.io/public-keys/cert-manager-keyring-2021-09-20-1020CF3C033D4F35BAE1C19E1226061C665DF13E.gpg
sudo env KUBECONFIG=/etc/rancher/k3s/k3s.yaml helm upgrade --install cert-manager \
  oci://quay.io/jetstack/charts/cert-manager --version v1.21.2 \
  --namespace cert-manager --create-namespace --set crds.enabled=true \
  --verify --keyring /tmp/cert-manager.gpg --wait --timeout 5m
```

The version and signature-verification options follow the [official cert-manager installation guide](https://cert-manager.io/docs/installation/helm/). Harbor's namespace uses default-deny networking. `networkPolicy.allowAcmeHttp01: true` permits only Traefik to reach temporary challenge pods on port 8089. The solver uses a ClusterIP Service, and its HTTP route takes precedence over the normal HTTPS redirect. Keep this allowance after the first certificate is issued so renewal can succeed.

### Deploy a reviewed release

Copy a source archive containing only tracked project files to the VM over SSH. Do not copy a Git credential, local databases or the development credential directory. The images normalize source and asset read permissions so a private archive extracted with `umask 077` still works with non-root processes. Generate fresh production credentials with the helper below; transfer the selected Gemini key separately into `.secrets/gemini-api-key.txt` with directory mode 700 and file mode 600. Without that key, the application starts but chat remains unavailable.

Build on the VM with a unique tag for this source revision, then import into k3s. Run one build at a time:

```sh
tag=release-YYYYMMDD-SHORTSHA
sudo docker build -t "harbor:$tag" -f Dockerfile .
sudo docker build -t "harbor-dashboard:$tag" -f dashboard/Dockerfile .
sudo docker build -t "harbor-wordpress:$tag" -f docker/wordpress/Dockerfile docker/wordpress
set -o pipefail
sudo docker save "harbor:$tag" "harbor-dashboard:$tag" "harbor-wordpress:$tag" | sudo k3s ctr images import -
sudo bash scripts/deploy-vps.sh CONSOLE_HOST STORE_HOST ADMIN_EMAIL "$tag"
```

The VPS helper operates only on `/etc/rancher/k3s/k3s.yaml`, requires one ready node, uses namespace and release `harbor`, discovers the node and pod addresses, and writes non-secret values to `.runtime/values-vps.json`. It creates private credentials, preserves existing application and upstream Secrets, applies the ingress configuration and deploys the same chart used locally. It refuses to generate passwords over existing data or proceed with credential files that differ from the deployed application Secret. Credential rotation remains a deliberate operation. It waits for WordPress bootstrap and every configured public certificate, but application acceptance testing remains a separate step. Keep `.secrets/development.json` and the Gemini key in a protected off-node backup.

On later releases, use the existing console and store hostnames. The helper preserves their current TLS configuration, including certificates retained for old-address redirects. It rejects hostname or service-role changes before touching credentials or cluster resources. A domain change requires a controlled migration: configure DNS, issue the new certificate, update the application and stored storefront URLs together, and verify redirects and authentication before publishing the new links.

`deploy/vps/traefik.yaml` changes the cluster's ingress controller, so use it only on the dedicated Harbor node. `deploy/vps/tls.yaml` creates a namespace-scoped issuer. Neither file creates billable cloud infrastructure. For a different ingress controller, an existing certificate, or a different namespace, use the manual configuration below.

### Manual deployment configuration

1. Install a supported k3s version with Traefik, local-path storage, the network-policy controller, and `--secrets-encryption`. The locally tested version is `v1.35.5+k3s1`. Follow the [official k3s installation guide](https://docs.k3s.io/quick-start); inspect and pin installer inputs for your environment. Keep Secret encryption keys and the cluster token in a protected backup.
2. Point two DNS names to the server. Provision a certificate and a TLS Secret or install cert-manager separately. TLS is required for real credentials on a public deployment. Configure Traefik to redirect HTTP to HTTPS. If using cert-manager's HTTP-01 challenge, explicitly allow Traefik to reach its solver pods through the namespace's default-deny NetworkPolicy, including during renewal. Merely setting `ingress.tls` does not issue a certificate or create a redirect.
3. Build the three Dockerfiles, tag them with a release version, and replace every `REPLACE_OWNER` image in `deploy/values-prod.yaml`. Application images contain source code. For a private registry, configure an image-pull Secret with `imagePullSecrets: [{name: registry-credentials}]`. Alternatively, import the images directly into containerd on a single-node host. Use images built for that host's CPU architecture.
4. Copy `deploy/values-prod.yaml` to a private values file. Set both hosts, both full public URLs, the TLS Secret, administrator identity, storage class and sizes. Set `networkPolicy.apiServerCidr` to the Kubernetes Service IP `/32` and `apiServerNodeCidr` to the control-plane internal IP `/32`. Default k3s uses `10.43.0.1/32` for the API Service, but verify rather than assuming. Set `gateway.trustedProxyCidrs` to only the pod CIDRs of the nodes running Traefik. The one-node default is `10.42.0.0/24`; verify it against the actual node. Keep `gateway.secureCookies: true` and `networkPolicy.enabled: true`.

Example discovery commands:

```sh
kubectl config get-contexts
kubectl config current-context
# Select the intended VPS context before continuing.
kubectl config use-context CONTEXT_NAME
kubectl get service kubernetes -n default -o jsonpath='{.spec.clusterIP}'
kubectl get nodes -o wide
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{": "}{.spec.podCIDRs}{"\n"}{end}'
kubectl get storageclass
```

Generate fresh production credentials on the target machine. `prepare-secrets.py` uses the Python standard library, creates files with private permissions, preserves an existing credential file, and prompts for the LLM key without echoing it:

```sh
umask 077
python3 scripts/prepare-secrets.py --namespace harbor --admin-email operator@example.com --prompt-gemini
kubectl create namespace harbor
kubectl apply -f .secrets/k8s-app-secret.json
kubectl create -f .secrets/k8s-upstream-secret.json
helm lint deploy/chart -f /secure/path/values-prod.yaml
helm upgrade --install harbor deploy/chart -n harbor \
  -f /secure/path/values-prod.yaml --wait --timeout 15m
kubectl -n harbor wait --for=condition=complete job/wordpress-bootstrap --timeout=600s
```

Set `gateway.adminEmail` to the same address passed to `prepare-secrets.py`. Set `wordpress.adminUser` if its generated name was changed. Namespace creation and the initial upstream Secret creation are first-install operations. On subsequent upgrades, **do not apply the seed upstream Secret again**: registry changes are stored in that mutable Secret. It is deliberately outside Helm release content so rollback cannot restore old upstream credentials.

Confirm application health, login, checkout and an external MCP call after deploying. Use the [public test target settings](development.md#testing-a-public-deployment) so the harnesses use that deployment's URLs, credentials and separate evidence directory. Verify the installed certificate independently. Chart rendering alone is not a VPS deployment test.

## Boundaries and Secret storage

Every workload has CPU/memory requests and limits, probes, and a non-root UID. The WordPress init container synchronizes packaged files into the persistent directory without changing its ownership. Containers drop capabilities and privilege escalation; only the gateway mounts a service-account token.

### Network access and proxy identity

Network policies deny ingress and egress by default. Explicit rules allow DNS; ingress-to-dashboard/gateway/store; gateway-to-upstreams/PostgreSQL; Woo-to-WordPress; and WordPress/bootstrap-to-MariaDB. Gateway egress also allows the configured Kubernetes API endpoint and public TCP 443 for Gemini and administrator-registered HTTPS MCP servers, excluding private/link-local ranges. Application URL validation adds DNS/IP checks and exact internal-host allowlisting. The sample policy and shipping pods cannot access the store or databases.

Uvicorn accepts forwarding headers only from `gateway.trustedProxyCidrs`, with the ingress NetworkPolicy further restricting gateway callers to Traefik pods in the configured namespace. These two controls work together: the CIDR survives an ingress pod restart, while the namespace and pod selector exclude unrelated pods sharing that range. The chart rejects wildcard proxy trust and proxy trust with NetworkPolicy disabled. Kubernetes network-policy enforcement must be enabled. Do not expose the gateway Service directly or grant untrusted users control over the ingress namespace or labels.

Keep Traefik's `forwardedHeaders.insecure` disabled and leave `forwardedHeaders.trustedIPs` empty unless there is a deliberate upstream HTTP proxy. For such a proxy, trust only its exact addresses and require it to sanitize forwarding headers. Preserve the original source IP on a public deployment, for example with Traefik's Service `externalTrafficPolicy: Local` and an appropriate load-balancer configuration. Uvicorn uses the nearest untrusted address in the forwarded chain, so client-supplied prefixes cannot select an arbitrary rate-limit bucket. Local k3d/Docker NAT and users sharing an external NAT may still share an address; the forwarding fix does not invent distinct identities for them. See the [Uvicorn proxy settings](https://uvicorn.dev/settings/#http) and [Traefik forwarded-header configuration](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/#forwarded-headers).

### Credential storage and rotation

The gateway Role can get and patch only `harbor-upstream-credentials`. It cannot list Secrets, read `harbor-app-secrets`, or create deployments. Each bundled upstream reads its own service authentication token from `harbor-app-secrets`; the gateway keeps a separate mutable copy. Editing or deleting a registry credential therefore cannot remove a token required for an upstream pod to start. To rotate a bundled service token, update its protected application Secret value, restart that service, and update its gateway credential. Application credentials are referenced through Secrets instead of being embedded in Helm values or release manifests. Kubernetes Secrets are not encryption by themselves; the cluster must enable encryption at rest. The local creation script includes the K3s encryption flag. For an existing unencrypted cluster, follow the [K3s migration procedure](https://docs.k3s.io/cli/secrets-encrypt#enable-secrets-encryption-on-an-existing-cluster).

```powershell
docker exec $projectServer k3s secrets-encrypt status
kubectl auth can-i get secret/harbor-upstream-credentials --as=system:serviceaccount:harbor:harbor-gateway -n harbor
kubectl auth can-i get secret/harbor-app-secrets --as=system:serviceaccount:harbor:harbor-gateway -n harbor
kubectl auth can-i list secrets --as=system:serviceaccount:harbor:harbor-gateway -n harbor
```

Expected results are encryption enabled, then `yes`, `no`, `no`. Encryption protects persisted API data, not an operator who already controls the node and encryption keys. Local backup files contain sensitive data and remain in `.secrets`.

### Live boundary and failure checks

Run the live boundary checks against the local cluster with `uv run python scripts/verify_network.py`. It uses working gateway connections as positive controls, checks that peer/untrusted pods cannot reach protected Services, verifies the exact RBAC verbs, and removes its temporary probe pod. No Secret contents are printed. During a maintenance window, `uv run python scripts/verify_failure.py` deletes the shipping upstream pod by temporarily scaling its controller to zero, verifies fast discovery and healthy calls to the other two upstreams, then restores the replica and revokes its temporary key. Run it only while other clients are idle. The scripts save evidence under `.runtime`.

During that same maintenance window, `uv run python scripts/verify_registry_lifecycle.py` deletes the seeded shipping registration, restarts the gateway and shipping pod, verifies the registration stays deleted while the upstream starts with its separate service token, then restores the registration and its permissions. The restored registration receives a new ID; historical audit entries retain the original ID. The bootstrap marker in PostgreSQL prevents deleted default connections from returning on application restarts.

## Backups, upgrades and rollback

### Create a backup

Before changing application schemas, WordPress, WooCommerce or database versions, pause writes and take coordinated backups. The local helper creates a PostgreSQL custom dump, a MariaDB SQL dump, a compressed WordPress content/config archive, and private snapshots of the application Secret, current mutable upstream Secret and local credential file, plus retained-installation settings when present:

```powershell
./scripts/backup.ps1
```

It writes under `.secrets/backups/<UTC timestamp>` with a directory ACL limited to the current Windows account. Database/archive files keep a `.partial` suffix until their command succeeds. Copy backups to protected storage off the node and verify restoration on a separate environment before relying on them. Without quiesced writes, these files are independent snapshots rather than one transaction across systems. PostgreSQL contains upstream credential references, so a database backup without the corresponding mutable Secret is incomplete.

On the dedicated Linux VPS, use:

```sh
sudo bash scripts/backup-vps.sh
```

This creates a new mode-700 directory under `.secrets/backups`, keeps failed outputs marked `.partial`, removes server-generated Secret metadata for recovery, and records Helm values and checksums alongside the database/content backups. Copy the completed directory off the VM. This is an application backup, not a snapshot of the k3s control plane. A replacement node can generate a new cluster and issue a new TLS certificate before restoring the application data.

### Upgrade or roll back application configuration

Upgrade immutable image references and review rendered changes before applying. Database engine upgrades require their own supported migration procedure. A Helm rollback changes manifests and image versions; it does not reverse database schema changes, restore deleted orders, or revert PVC contents.

For a local persistence check, `uv run python scripts/verify_release.py` records non-sensitive user/product/order IDs and PVC identities, restarts WordPress and both database pods, and checks that those records persist. It then changes only the gateway CPU limit through a new Helm revision, rolls back to the compatible revision it started from, and checks the original configuration and records. This checks retained data and configuration rollback with unchanged images. Use the separate version drill below to test replacement application binaries. Run it only while other clients are idle.

#### Test an application upgrade and rollback

`scripts/verify_application_release.py` performs a separate A → B → A drill on an explicitly selected isolated release. Provision that release with its own namespace, Secrets, PostgreSQL, MariaDB, WordPress and PVCs. Use separate console/store hostnames and seed an administrator, a member, products and an order. Never label a shared or production namespace as the drill target. The script does not create or delete environments.

Build A from the baseline source and B from the candidate source, using distinct version tags or registry digests for both the shared gateway/upstream image and dashboard image. Install A using the same chart and values the drill will use for B. Keep the database models and migration code identical. Build sequentially on small hosts; a second namespace still needs independent data workloads and volumes.

Create a private JSON credential file containing `admin_email`, `admin_password`, `member_email` and `member_password` for this isolated release. Keep the exact source checkout used to build B available to `--candidate-source`. The script checks A's running schema source against that checkout before any mutation, and checks B's running application source against it after deployment.

```sh
# Label only the already-provisioned, disposable verification namespace.
kubectl --context k3d-harbor-release label namespace harbor-release \
  harbor.local/release-verification=isolated

# Read-only preflight. Replace every example target and image with your prepared values.
uv run python scripts/verify_application_release.py \
  --context k3d-harbor-release --namespace harbor-release --release harbor-release \
  --base http://release.localhost:8080 \
  --a-app harbor:baseline-v1 --a-dashboard harbor-dashboard:baseline-v1 \
  --b-app harbor:candidate-v2 --b-dashboard harbor-dashboard:candidate-v2 \
  --candidate-source . --credentials .secrets/release-drill.json \
  --report .runtime/application-release.json
```

Repeat that command with `--execute` to run the drill while the isolated store is idle. Its Helm upgrade changes only the five application container images; preflight rejects any other rendered resource change. Hooks are disabled, so WordPress bootstrap does not run again. Kubernetes readiness, direct readiness endpoints, exact runtime `imageID` values and application payload hashes are recorded for each stage. Changing only an image tag or OCI metadata fails the check.

The same two temporary personal keys and browser sessions must work at A, B and restored A. Each stage checks member write denial, administrator/member discovery, and real read calls through all three upstreams. It compares user roles, registry permission fingerprints, Alembic versions, product/order content fingerprints, the privacy notice, retained PVC UIDs and bound volume names. The data pods must remain unchanged. Customer data, passwords and tokens are excluded from the report.

Once an upgrade is attempted, the script attempts rollback in `finally`, including after a failed Helm upgrade or failed B checks, then revokes its temporary keys and sessions. It writes failure evidence even when recovery fails and exits nonzero; inspect `rollback_verified` before treating a failed drill as recovered. Forced process termination or host loss cannot execute cleanup. Preserve the isolated environment and report for diagnosis in that case. A passed result verifies compatible application binaries were replaced and restored with retained data. Database downgrades and backup restoration require separate checks. Retain the private report with the release artifacts.

```sh
helm history harbor -n harbor
helm upgrade harbor deploy/chart -n harbor -f /secure/path/values-prod.yaml --wait --timeout 15m
# Choose an actually compatible revision from history after reviewing migrations.
helm rollback harbor PREVIOUS_REVISION -n harbor --wait --timeout 15m
```

### Restore data

Restore into a separate recovery cluster or namespace first. Use database and application versions compatible with the backup; do not upgrade while diagnosing a restore.

1. Restore the saved Secrets into the recovery namespace. The Windows backup's `kubernetes-secrets.json` already strips server-generated metadata; change its namespace before applying it. A raw Linux export also needs resource versions, UIDs and timestamps removed.
2. Create deliberately empty PVCs and start only the database workloads. Keep the gateway, MCP upstreams, WordPress and the `wordpress-bootstrap` Job stopped until imports finish. A normal bootstrap is not a restore command.
3. Import `gateway.dump` with `pg_restore -U harbor -d harbor --clean --if-exists`, using the retained PostgreSQL identity if different. Import `wordpress.sql` through `mariadb --user=wordpress --password="$MARIADB_PASSWORD" wordpress` inside the database container.
4. Extract `wordpress-content.tar.gz` into the mounted WordPress PVC using a temporary recovery pod with UID/GID 33. Preserve file contents and ownership. Transfer the PostgreSQL dump and archive as binary streams; do not decode them through a text pipeline.
5. Start compatible application workloads and validate user and store record counts, login, checkout and MCP calls before switching traffic. Keep the original volumes and backup until recovery is verified.

Create recovery resources through Helm where possible. Manually applied resources can retain field ownership that conflicts with later Helm upgrades. Review the affected resources and their `managedFields` before transferring ownership; do not add force flags to routine deployments.

Restore `development.json` and `gemini-api-key.txt` privately before using local maintenance scripts. Restore saved `local-settings.json` to `.runtime`, adjusting the target cluster and namespace for the recovery environment while retaining the correct database identity. These imports replace data and remain deliberate operator steps.

PVCs have `helm.sh/resource-policy: keep`. `uninstall.ps1` leaves them and both external Secrets in place. Deleting the k3d cluster or its Docker volumes is a separate destructive action and is intentionally not included in the teardown helper.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| Docker command fails | Start Docker Desktop and confirm the Linux engine is ready |
| Bootstrap reports missing credentials for retained data | Restore the private credential file from backup; do not generate new passwords |
| No dashboard or store response | Check the selected cluster, ingress ports and workload readiness |
| MCP connection is Connected but a commerce call fails | Check WordPress and MariaDB; discovery does not call WooCommerce REST |
| Chat reports unavailable, quota or provider errors | Check the configured Gemini key and provider availability |
| Browser shows old code after source edits | Rebuild images, deploy and reload; source changes alone do not update pods |

### Repair non-loopback port bindings

Bootstrap refuses to resume a cluster whose published ports bind outside loopback. To repair that condition, take a backup with `./scripts/backup.ps1`, preview `./scripts/limit-local-bindings.ps1`, then run it with `-Apply` during a maintenance window. The helper replaces only the project's load balancer, preserves the server and data volumes, and checks their identities afterward. If interrupted, rerun it to resume from its private state file. Do not delete the cluster to change bindings.

Inspect `docker inspect $projectLoadBalancer --format '{{json .HostConfig.PortBindings}}'` using the container selected above. All published entries must use `127.0.0.1`. Confirm that the dashboard and API respond on loopback and reject connections through the host's non-loopback address.

### Cluster resume and stale proxy addresses

Use `stop.ps1` and `bootstrap.ps1` for the normal local lifecycle. Resuming an existing cluster checks the host API with bounded retries and refreshes only the project load balancer if the API is healthy inside the node but unavailable on the host. Deployment then recomputes the narrow API-server egress CIDR. Missing local credentials with retained project Secrets or volumes stop bootstrap instead of silently generating new database passwords. The isolated recovery and credential-guard cases can be run with `pwsh -File scripts/test-local-recovery.ps1`.

A manual `docker restart` of only the k3d server can change its Docker-network IP while the project load balancer still caches the old address. For that specific condition outside normal bootstrap, restart only the project's load-balancer container selected in [the local context procedure](#select-the-local-context), wait for its proxy to start, then run `deploy.ps1`. Node readiness alone does not prove upstream credentials are reachable. Do not restart Docker Desktop or delete other containers as a workaround.

```powershell
docker exec $projectServer kubectl get --raw /readyz
# Only when diagnosing that specific stale proxy condition:
docker restart $projectLoadBalancer
./scripts/deploy.ps1
```

### Resource pressure and workload diagnostics

On a resource-constrained development host, serialize image builds and browser runs. Severe host memory pressure can delay the whole Docker VM even when individual workload limits are respected. The probes allow 3 to 5 seconds and require sustained liveness failures before restarting; they do not make an overloaded host reliable.

```powershell
kubectl -n harbor get pods,pvc,ingress
kubectl -n harbor get events --sort-by=.metadata.creationTimestamp
kubectl -n harbor logs deployment/gateway --tail=80
kubectl -n harbor logs job/wordpress-bootstrap --tail=80
```

An upstream can report Connected while the WooCommerce REST call itself fails; MCP service health and store health are separate boundaries. Tool errors surface that failure and are audited. A live pod with an unreachable dependency should not trigger a restart cascade. Check the dashboard's last-error field, the relevant workload logs, namespace policies and readiness before changing credentials. Do not paste Secret contents into issues or screenshots.
