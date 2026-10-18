# Operations and deployment

The local target is one k3d server backed by Docker Desktop. It runs the same Helm chart as the k3s VPS example. A single node and local-path storage are deliberate demo constraints: they provide persistence across pod restarts, not node failure recovery or high availability.

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

Before the first Harbor upgrade, copy the current application Secret and mutable registry Secret to `harbor-app-secrets` and `harbor-upstream-credentials` in the retained namespace, preserving their contents and access restrictions. Do not regenerate passwords or replace registry credentials with seed values. Review the settings and Secret migration before invoking bootstrap.

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

### Repair older port bindings

Older clusters created before the loopback fix retain their original Docker bindings. Startup refuses to resume such a cluster automatically. During an idle maintenance window, take a backup with `./scripts/backup.ps1`, preview `./scripts/limit-local-bindings.ps1`, then run it with `-Apply`. This uses k3d's port-edit operation to replace only the project's load balancer twice: first remove its wildcard publications, then add loopback publications at the same ports. The k3s server and its data volumes stay in place. The helper checks that the server ID, start time and mounts did not change, and changes only the project's kubeconfig API address to `127.0.0.1`. If interrupted between those steps, rerun the same command; `.runtime/local-binding-migration.json` retains the non-sensitive API port and server identity needed to finish. Do not delete the cluster to change bindings.

Inspect the final result with `docker inspect $projectLoadBalancer --format '{{json .HostConfig.PortBindings}}'`, using the container selected above. All three entries must use `127.0.0.1`. Confirm that the dashboard and API work on loopback and that connecting to the host's non-loopback address fails. Do not dump all container labels or environment variables, which can contain cluster credentials.

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

The WordPress bootstrap Job runs after installation and upgrades. It installs WordPress once, activates the packaged WooCommerce and Storefront versions, and creates missing API credentials. Starter products and classic cart/checkout content are initialized only before the `harbor_seed_complete` marker is set; later product deletions and page edits remain intact. Earlier seed markers remain compatible without replaying initialization. It configures INR, the Asia/Kolkata timezone, COD and the sample shipping table, and turns off WooCommerce coming-soon mode so anonymous visitors can browse. If no published privacy page exists, it publishes a short demo data-use notice once and links checkout to it; later edits remain intact. The demo intentionally has no outbound email provider. The original product SVGs are packaged assets; arbitrary SVG uploads are not enabled. Products without media have a labeled typographic card rather than a broken-image icon.

The tested store image pins WordPress 7.1.3, PHP 8.3, WooCommerce 11.2.1, Storefront 4.6.2 and WP-CLI 2.12.0. Plugin and theme downloads have checked SHA256 values in the Dockerfile. The database images are MariaDB 11.4.13 and PostgreSQL 17.11. Version changes should go through backup and compatibility checks.

## VPS configuration

No public VPS deployment is claimed. These steps describe the production-like target using the same chart. The server needs sufficient memory, disk space, inbound TCP 80/443, and a private administration path. Restrict port 6443 to operators. Do not expose database or upstream ports.

1. Install a supported k3s version with Traefik, local-path storage, the network-policy controller, and `--secrets-encryption`. The locally tested version is `v1.35.5+k3s1`. Follow the [official k3s installation guide](https://docs.k3s.io/quick-start); inspect and pin installer inputs for your environment. Keep Secret encryption keys and the cluster token in a protected backup.
2. Point two DNS names to the server. Provision a certificate and a TLS Secret or install cert-manager separately. TLS is required for real credentials on a public deployment.
3. Build the three Dockerfiles, tag them with a release version, push to a registry the node can pull, and replace every `REPLACE_OWNER` image in `deploy/values-prod.yaml`. Public images avoid a registry credential; private images require an image-pull Secret and `imagePullSecrets: [{name: registry-credentials}]` in the private values file.
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

Confirm application health, login, checkout and an external MCP call after deploying. The local acceptance script supports `--base` for a different console URL. Verify the installed certificate independently. Chart rendering alone is not a VPS deployment test.

## Boundaries and Secret storage

Every workload has CPU/memory requests and limits, probes, and a non-root UID. The WordPress init container synchronizes packaged files into the persistent directory without changing its ownership. Containers drop capabilities and privilege escalation; only the gateway mounts a service-account token.

### Network access and proxy identity

Network policies deny ingress and egress by default. Explicit rules allow DNS; ingress-to-dashboard/gateway/store; gateway-to-upstreams/PostgreSQL; Woo-to-WordPress; and WordPress/bootstrap-to-MariaDB. Gateway egress also allows the configured Kubernetes API endpoint and public TCP 443 for Gemini and administrator-registered HTTPS MCP servers, excluding private/link-local ranges. Application URL validation adds DNS/IP checks and exact internal-host allowlisting. The sample policy and shipping pods cannot access the store or databases.

Uvicorn accepts forwarding headers only from `gateway.trustedProxyCidrs`, with the ingress NetworkPolicy further restricting gateway callers to Traefik pods in the configured namespace. These two controls work together: the CIDR survives an ingress pod restart, while the namespace and pod selector exclude unrelated pods sharing that range. The chart rejects wildcard proxy trust and proxy trust with NetworkPolicy disabled. Kubernetes network-policy enforcement must be enabled. Do not expose the gateway Service directly or grant untrusted users control over the ingress namespace or labels.

Keep Traefik's `forwardedHeaders.insecure` disabled and leave `forwardedHeaders.trustedIPs` empty unless there is a deliberate upstream HTTP proxy. For such a proxy, trust only its exact addresses and require it to sanitize forwarding headers. Preserve the original source IP on a public deployment, for example with Traefik's Service `externalTrafficPolicy: Local` and an appropriate load-balancer configuration. Uvicorn uses the nearest untrusted address in the forwarded chain, so client-supplied prefixes cannot select an arbitrary rate-limit bucket. Local k3d/Docker NAT and users sharing an external NAT may still share an address; the forwarding fix does not invent distinct identities for them. See the [Uvicorn proxy settings](https://uvicorn.dev/settings/#http) and [Traefik forwarded-header configuration](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/#forwarded-headers).

### Credential storage and rotation

The gateway Role can get and patch only `harbor-upstream-credentials`. It cannot list Secrets, read `harbor-app-secrets`, or create deployments. Each bundled upstream reads its own service authentication token from `harbor-app-secrets`; the gateway keeps a separate mutable copy. Editing or deleting a registry credential therefore cannot remove a token required for an upstream pod to start. To rotate a bundled service token, update its protected application Secret value, restart that service, and update its gateway credential. Application credentials are referenced through Secrets instead of being embedded in Helm values or release manifests. Kubernetes Secrets are not encryption by themselves; the cluster must enable encryption at rest. The local creation script includes the k3s flag, and the existing local cluster was migrated using the [official existing-cluster procedure](https://docs.k3s.io/cli/secrets-encrypt#enable-secrets-encryption-on-an-existing-cluster).

```powershell
docker exec $projectServer k3s secrets-encrypt status
kubectl auth can-i get secret/harbor-upstream-credentials --as=system:serviceaccount:harbor:harbor-gateway -n harbor
kubectl auth can-i get secret/harbor-app-secrets --as=system:serviceaccount:harbor:harbor-gateway -n harbor
kubectl auth can-i list secrets --as=system:serviceaccount:harbor:harbor-gateway -n harbor
```

Expected results are encryption enabled, then `yes`, `no`, `no`. Encryption protects persisted API data, not an operator who already controls the node and encryption keys. Local backup files contain sensitive data and remain in `.secrets`.

### Live boundary and failure checks

Run the live boundary checks against the local cluster with `uv run python scripts/verify_network.py`. It uses working gateway connections as positive controls, checks that peer/untrusted pods cannot reach protected Services, verifies the exact RBAC verbs, and removes its temporary probe pod. No Secret contents are printed. During a planned demo interruption, `uv run python scripts/verify_failure.py` deletes the shipping upstream pod by temporarily scaling its controller to zero, verifies fast discovery and healthy calls to the other two upstreams, then restores the replica and revokes its temporary key. Do not run this while someone is placing orders or recording a demo. The scripts save evidence under `.runtime`.

During that same maintenance window, `uv run python scripts/verify_registry_lifecycle.py` deletes the seeded shipping registration, restarts the gateway and shipping pod, verifies the registration stays deleted while the upstream starts with its separate service token, then restores the registration and its permissions. The restored registration receives a new ID; historical audit entries retain the original ID. The bootstrap marker in PostgreSQL prevents deleted default connections from returning on application restarts.

## Backups, upgrades and rollback

### Create a backup

Before changing application schemas, WordPress, WooCommerce or database versions, pause writes and take coordinated backups. The local helper creates a PostgreSQL custom dump, a MariaDB SQL dump, a compressed WordPress content/config archive, and private snapshots of the application Secret, current mutable upstream Secret and local credential file, plus retained-installation settings when present:

```powershell
./scripts/backup.ps1
```

It writes under `.secrets/backups/<UTC timestamp>` with a directory ACL limited to the current Windows account. Database/archive files keep a `.partial` suffix until their command succeeds. Copy backups to protected storage off the node and verify restoration on a separate environment before relying on them. Without quiesced writes, these files are independent snapshots rather than one transaction across systems. PostgreSQL contains upstream credential references, so a database backup without the corresponding mutable Secret is incomplete.

Equivalent Linux backup commands:

```sh
umask 077
mkdir -p /secure/backups/harbor
kubectl -n harbor get secret harbor-app-secrets harbor-upstream-credentials -o json > /secure/backups/harbor/kubernetes-secrets.json
kubectl -n harbor exec deployment/postgres -- pg_dump -U harbor -d harbor -Fc > /secure/backups/harbor/gateway.dump
kubectl -n harbor exec deployment/mariadb -- sh -c 'mariadb-dump --single-transaction --user=wordpress --password="$MARIADB_PASSWORD" wordpress' > /secure/backups/harbor/wordpress.sql
kubectl -n harbor exec deployment/wordpress -- tar -czf - -C /var/www/html wp-content wp-config.php > /secure/backups/harbor/wordpress-content.tar.gz
```

### Upgrade or roll back application configuration

Upgrade immutable image references and review rendered changes before applying. Database engine upgrades require their own supported migration procedure. A Helm rollback changes manifests and image versions; it does not reverse database schema changes, restore deleted orders, or revert PVC contents.

For the local acceptance scenario, `uv run python scripts/verify_release.py` records non-sensitive user/product/order IDs and PVC identities, restarts WordPress and both database pods, and checks that those records persist. It then changes only the gateway CPU limit through a new Helm revision, rolls back to the compatible revision it started from, and checks the original configuration and records. This deliberately uses unchanged image tags and proves configuration rollback, not old application-binary or database recovery. Run it only while other clients are idle.

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

Keep recovery resources under Helm's field manager from the start where possible. Manual `kubectl` application can leave shared field ownership that conflicts with later Helm 4 upgrades. Inspect `managedFields`, compare the live resource with the intended chart, and deliberately transfer ownership only for the known recovery-created resources. Removing a temporary recovery manager's entry requires a metadata backup and verification that no resource specification or content changes. `--force-conflicts` alone may leave unchanged fields shared, so it is not a complete ownership handoff. Do not clear unrelated managers or add force flags to normal deployment commands.

Restore `development.json` and `gemini-api-key.txt` privately before using local maintenance scripts. Restore saved `local-settings.json` to `.runtime`, adjusting the target cluster and namespace for the recovery environment while retaining the correct database identity. These imports replace data and remain deliberate operator steps.

PVCs have `helm.sh/resource-policy: keep`. `uninstall.ps1` leaves them and both external Secrets in place. Deleting the k3d cluster or its Docker volumes is a separate destructive action and is intentionally not included in the teardown helper.

The [verification record](verification.md) dates the completed operational checks and distinguishes pod replacement, configuration rollback and actual backup restoration.

## Troubleshooting

| Symptom | First check |
| --- | --- |
| Docker command fails | Start Docker Desktop and confirm the Linux engine is ready |
| Bootstrap reports missing credentials for retained data | Restore the private credential file from backup; do not generate new passwords |
| No dashboard or store response | Check the selected cluster, ingress ports and workload readiness |
| MCP connection is Connected but a commerce call fails | Check WordPress and MariaDB; discovery does not call WooCommerce REST |
| Chat reports unavailable, quota or provider errors | Check the configured Gemini key and provider availability |
| Browser shows old code after source edits | Rebuild images, deploy and reload; source changes alone do not update pods |

### Cluster resume and stale proxy addresses

Use `stop.ps1` and `bootstrap.ps1` for the normal local lifecycle. Resuming an existing cluster now checks the host API with bounded retries and refreshes only the project load balancer if the API is healthy inside the node but unavailable on the host. Deployment then recomputes the narrow API-server egress CIDR. Missing local credentials with retained project Secrets or volumes stop bootstrap instead of silently generating new database passwords. The isolated recovery and credential-guard cases can be run with `pwsh -File scripts/test-local-recovery.ps1`.

A manual `docker restart` of only the k3d server can change its Docker-network IP while the project load balancer still caches the old address. For that specific condition outside normal bootstrap, restart only the project's load-balancer container selected in [the local context procedure](#select-the-local-context), wait for its proxy to start, then run `deploy.ps1`. Node readiness alone does not prove upstream credentials are reachable. Do not restart Docker Desktop or delete other containers as a workaround.

```powershell
docker exec $projectServer kubectl get --raw /readyz
# Only when diagnosing that specific stale proxy condition:
docker restart $projectLoadBalancer
./scripts/deploy.ps1
```

### Resource pressure and workload diagnostics

On a 16 GiB Windows host, serialize image builds and browser runs. Severe host memory pressure can delay the whole Docker VM even when individual workload limits are respected. The probes allow 3 to 5 seconds and require sustained liveness failures before restarting; they do not make an overloaded host reliable.

```powershell
kubectl -n harbor get pods,pvc,ingress
kubectl -n harbor get events --sort-by=.metadata.creationTimestamp
kubectl -n harbor logs deployment/gateway --tail=80
kubectl -n harbor logs job/wordpress-bootstrap --tail=80
```

An upstream can report Connected while the WooCommerce REST call itself fails; MCP service health and store health are separate boundaries. Tool errors surface that failure and are audited. A live pod with an unreachable dependency should not trigger a restart cascade. Check the dashboard's last-error field, the relevant workload logs, namespace policies and readiness before changing credentials. Do not paste Secret contents into issues or screenshots.
