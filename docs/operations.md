# Operations and deployment

The local target is one k3d server backed by Docker Desktop. It runs the same Helm chart as the k3s VPS example. A single node and local-path storage are deliberate demo constraints: they provide persistence across pod restarts, not node failure recovery or high availability.

## Local lifecycle

Use PowerShell 7 from the repository root. Docker Desktop must be running Linux containers.

```powershell
./scripts/install-tools.ps1
./scripts/bootstrap.ps1
```

Helm 4.3.1 and k3d 5.9.0 are installed only in `.tools`, with SHA256 checks against official release files. The cluster is `urumi-commerce`, the kubectl context is `k3d-urumi-commerce`, and the namespace and release are both `commerce`. The server uses the tested `rancher/k3s:v1.35.5-k3s1` image and a 6 GiB memory cap. Existing Docker containers and clusters are not removed.

`bootstrap.ps1` creates random credentials in the access-restricted, ignored `.secrets/development.json`, creates Kubernetes Secrets, builds and imports three images, and installs the chart. Put a Gemini key in `.secrets/gemini-api-key.txt` first, or configure it later and rerun bootstrap. No script prints passwords. Keep the generated credential file: changing it does not rotate credentials already stored in PostgreSQL or MariaDB.

Local names use the `.localhost` domain, so an administrator hosts-file edit is unnecessary. New clusters explicitly publish ingress ports 8080/8443 and the dynamically assigned Kubernetes API port on `127.0.0.1`. The socket binding, rather than the hostname, limits access to this computer:

| Application | Address |
| --- | --- |
| Dashboard | `http://console.localhost:8080` |
| Gateway | `http://console.localhost:8080/mcp` |
| Store | `http://store.localhost:8080` |
| Store admin | `http://store.localhost:8080/wp-admin/` |

Only Traefik ingress and the Kubernetes administration API are published, both on loopback. The WordPress Service listens on port 80 and forwards to non-root Apache on 8080. Gateway, dashboard, upstreams, and databases are all ClusterIP Services. The upstream port is 8001 and has no Ingress route.

Older clusters created before the loopback fix retain their original Docker bindings. Startup refuses to resume such a cluster automatically. During an idle maintenance window, take a backup with `./scripts/backup.ps1`, preview `./scripts/limit-local-bindings.ps1`, then run it with `-Apply`. This uses k3d's port-edit operation to replace only the project's load balancer twice: first remove its wildcard publications, then add loopback publications at the same ports. The k3s server and its data volumes stay in place. The helper checks that the server ID, start time and mounts did not change, and changes only the project's kubeconfig API address to `127.0.0.1`. If interrupted between those steps, rerun the same command; `.runtime/local-binding-migration.json` retains the non-sensitive API port and server identity needed to finish. Do not delete the cluster to change bindings.

Inspect the final result with `docker inspect k3d-urumi-commerce-serverlb --format '{{json .HostConfig.PortBindings}}'`. All three entries must use `127.0.0.1`. Confirm that the dashboard and API work on loopback and that connecting to the host's non-loopback address fails. Do not dump all container labels or environment variables, which can contain cluster credentials.

```powershell
# Rebuild and deploy after a source change.
./scripts/build-images.ps1
./scripts/deploy.ps1

# Stop the cluster while retaining all data.
./scripts/stop.ps1

# Remove workloads but retain the namespace, generated Secrets and PVCs.
./scripts/uninstall.ps1
```

The deployment script creates `.runtime/values-runtime.yaml` with the actual node IP for API-server egress, its pod CIDRs for ingress proxy trust, and a new rollout revision. This makes a local `:dev` image rebuild restart the application pods. Production should use immutable version tags or digests instead.

The WordPress bootstrap Job runs after installation and upgrades. It installs WordPress once, activates the packaged WooCommerce and Storefront versions, and creates missing API credentials. Starter products and classic cart/checkout content are initialized only before the `commerce_seed_complete` marker is set; later product deletions and page edits remain intact. It configures INR, the Asia/Kolkata timezone, COD and the sample shipping table, and turns off WooCommerce coming-soon mode so anonymous visitors can browse. If no published privacy page exists, it publishes a short demo data-use notice once and links checkout to it; later edits remain intact. The demo intentionally has no outbound email provider. The original product SVGs are packaged assets; arbitrary SVG uploads are not enabled. Products without media have a labeled typographic card rather than a broken-image icon.

The tested store image pins WordPress 7.1.3, PHP 8.3, WooCommerce 11.2.1, Storefront 4.6.2 and WP-CLI 2.12.0. Plugin and theme downloads have checked SHA256 values in the Dockerfile. The database images are MariaDB 11.4.13 and PostgreSQL 17.11. Version changes should go through backup and compatibility checks.

## VPS configuration

No public VPS deployment is claimed. These steps describe the production-like target using the same chart. The server needs sufficient memory, disk space, inbound TCP 80/443, and a private administration path. Restrict port 6443 to operators. Do not expose database or upstream ports.

1. Install a supported k3s version with Traefik, local-path storage, the network-policy controller, and `--secrets-encryption`. The locally tested version is `v1.35.5+k3s1`. Follow the [official k3s installation guide](https://docs.k3s.io/quick-start); inspect and pin installer inputs for your environment. Keep Secret encryption keys and the cluster token in a protected backup.
2. Point two DNS names to the server. Provision a certificate and a TLS Secret or install cert-manager separately. TLS is required for real credentials on a public deployment.
3. Build the three Dockerfiles, tag them with a release version, push to a registry the node can pull, and replace every `REPLACE_OWNER` image in `deploy/values-prod.yaml`. Public images avoid a registry credential; private images require an image-pull Secret and `imagePullSecrets: [{name: registry-credentials}]` in the private values file.
4. Copy `deploy/values-prod.yaml` to a private values file. Set both hosts, both full public URLs, the TLS Secret, administrator identity, storage class and sizes. Set `networkPolicy.apiServerCidr` to the Kubernetes Service IP `/32` and `apiServerNodeCidr` to the control-plane internal IP `/32`. Default k3s uses `10.43.0.1/32` for the API Service, but verify rather than assuming. Set `gateway.trustedProxyCidrs` to only the pod CIDRs of the nodes running Traefik. The one-node default is `10.42.0.0/24`; verify it against the actual node. Keep `gateway.secureCookies: true` and `networkPolicy.enabled: true`.

Example discovery commands:

```sh
kubectl get service kubernetes -n default -o jsonpath='{.spec.clusterIP}'
kubectl get nodes -o wide
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{": "}{.spec.podCIDRs}{"\n"}{end}'
kubectl get storageclass
```

Generate fresh production credentials on the target machine. `prepare-secrets.py` uses the Python standard library, creates files with private permissions, preserves an existing credential file, and prompts for the LLM key without echoing it:

```sh
umask 077
python3 scripts/prepare-secrets.py --namespace commerce --admin-email operator@example.com --prompt-gemini
kubectl create namespace commerce
kubectl apply -f .secrets/k8s-app-secret.json
kubectl create -f .secrets/k8s-upstream-secret.json
helm lint deploy/chart -f /secure/path/values-prod.yaml
helm upgrade --install commerce deploy/chart -n commerce \
  -f /secure/path/values-prod.yaml --wait --timeout 15m
kubectl -n commerce wait --for=condition=complete job/wordpress-bootstrap --timeout=600s
```

Set `gateway.adminEmail` to the same address passed to `prepare-secrets.py`. Set `wordpress.adminUser` if its generated name was changed. Namespace creation and the initial upstream Secret creation are first-install operations. On subsequent upgrades, **do not apply the seed upstream Secret again**: registry changes are stored in that mutable Secret. It is deliberately outside Helm release content so rollback cannot restore old upstream credentials.

Confirm application health, login, checkout and an external MCP call after deploying. The local acceptance script supports `--base` for a different console URL. Verify the installed certificate independently. Chart rendering alone is not a VPS deployment test.

## Boundaries and Secret storage

Every workload has CPU/memory requests and limits, probes, and a non-root UID. The WordPress init container synchronizes packaged files into the persistent directory without changing its ownership. Containers drop capabilities and privilege escalation; only the gateway mounts a service-account token.

Network policies deny ingress and egress by default. Explicit rules allow DNS; ingress-to-dashboard/gateway/store; gateway-to-upstreams/PostgreSQL; Woo-to-WordPress; and WordPress/bootstrap-to-MariaDB. Gateway egress also allows the configured Kubernetes API endpoint and public TCP 443 for Gemini and administrator-registered HTTPS MCP servers, excluding private/link-local ranges. Application URL validation adds DNS/IP checks and exact internal-host allowlisting. The sample policy and shipping pods cannot access the store or databases.

Uvicorn accepts forwarding headers only from `gateway.trustedProxyCidrs`, with the ingress NetworkPolicy further restricting gateway callers to Traefik pods in the configured namespace. These two controls work together: the CIDR survives an ingress pod restart, while the namespace and pod selector exclude unrelated pods sharing that range. The chart rejects wildcard proxy trust and proxy trust with NetworkPolicy disabled. Kubernetes network-policy enforcement must be enabled. Do not expose the gateway Service directly or grant untrusted users control over the ingress namespace or labels.

Keep Traefik's `forwardedHeaders.insecure` disabled and leave `forwardedHeaders.trustedIPs` empty unless there is a deliberate upstream HTTP proxy. For such a proxy, trust only its exact addresses and require it to sanitize forwarding headers. Preserve the original source IP on a public deployment, for example with Traefik's Service `externalTrafficPolicy: Local` and an appropriate load-balancer configuration. Uvicorn uses the nearest untrusted address in the forwarded chain, so client-supplied prefixes cannot select an arbitrary rate-limit bucket. Local k3d/Docker NAT and users sharing an external NAT may still share an address; the forwarding fix does not invent distinct identities for them. See the [Uvicorn proxy settings](https://uvicorn.dev/settings/#http) and [Traefik forwarded-header configuration](https://doc.traefik.io/traefik/reference/install-configuration/entrypoints/#forwarded-headers).

The gateway Role can get and patch only `commerce-upstream-credentials`. It cannot list Secrets, read `commerce-app-secrets`, or create deployments. Each bundled upstream reads its own service authentication token from `commerce-app-secrets`; the gateway keeps a separate mutable copy. Editing or deleting a registry credential therefore cannot remove a token required for an upstream pod to start. To rotate a bundled service token, update its protected application Secret value, restart that service, and update its gateway credential. Application credentials are referenced through Secrets instead of being embedded in Helm values or release manifests. Kubernetes Secrets are not encryption by themselves; the cluster must enable encryption at rest. The local creation script includes the k3s flag, and the existing local cluster was migrated using the [official existing-cluster procedure](https://docs.k3s.io/cli/secrets-encrypt#enable-secrets-encryption-on-an-existing-cluster).

```powershell
docker exec k3d-urumi-commerce-server-0 k3s secrets-encrypt status
kubectl auth can-i get secret/commerce-upstream-credentials --as=system:serviceaccount:commerce:commerce-gateway -n commerce
kubectl auth can-i get secret/commerce-app-secrets --as=system:serviceaccount:commerce:commerce-gateway -n commerce
kubectl auth can-i list secrets --as=system:serviceaccount:commerce:commerce-gateway -n commerce
```

Expected results are encryption enabled, then `yes`, `no`, `no`. Encryption protects persisted API data, not an operator who already controls the node and encryption keys. Local backup files contain sensitive data and remain in `.secrets`.

Run the live boundary checks against the local cluster with `uv run python scripts/verify_network.py`. It uses working gateway connections as positive controls, checks that peer/untrusted pods cannot reach protected Services, verifies the exact RBAC verbs, and removes its temporary probe pod. No Secret contents are printed. During a planned demo interruption, `uv run python scripts/verify_failure.py` deletes the shipping upstream pod by temporarily scaling its controller to zero, verifies fast discovery and healthy calls to the other two upstreams, then restores the replica and revokes its temporary key. Do not run this while someone is placing orders or recording a demo. The scripts save evidence under `.runtime`.

During that same maintenance window, `uv run python scripts/verify_registry_lifecycle.py` deletes the seeded shipping registration, restarts the gateway and shipping pod, verifies the registration stays deleted while the upstream starts with its separate service token, then restores the registration and its permissions. The restored registration receives a new ID; historical audit entries retain the original ID. The bootstrap marker in PostgreSQL prevents deleted default connections from returning on application restarts.

## Backups, upgrades and rollback

Before changing application schemas, WordPress, WooCommerce or database versions, pause writes and take coordinated backups. The local helper creates a PostgreSQL custom dump, a MariaDB SQL dump, a compressed WordPress content/config archive, and private snapshots of the application Secret, current mutable upstream Secret and local credential file:

```powershell
./scripts/backup.ps1
```

It writes under `.secrets/backups/<UTC timestamp>` with a directory ACL limited to the current Windows account. Database/archive files keep a `.partial` suffix until their command succeeds. Copy backups to protected storage off the node and verify restoration on a separate environment before relying on them. Without quiesced writes, these files are independent snapshots rather than one transaction across systems. PostgreSQL contains upstream credential references, so a database backup without the corresponding mutable Secret is incomplete.

Equivalent Linux backup commands:

```sh
umask 077
mkdir -p /secure/backups/commerce
kubectl -n commerce get secret commerce-app-secrets commerce-upstream-credentials -o json > /secure/backups/commerce/kubernetes-secrets.json
kubectl -n commerce exec deployment/postgres -- pg_dump -U commerce -d commerce -Fc > /secure/backups/commerce/gateway.dump
kubectl -n commerce exec deployment/mariadb -- sh -c 'mariadb-dump --single-transaction --user=wordpress --password="$MARIADB_PASSWORD" wordpress' > /secure/backups/commerce/wordpress.sql
kubectl -n commerce exec deployment/wordpress -- tar -czf - -C /var/www/html wp-content wp-config.php > /secure/backups/commerce/wordpress-content.tar.gz
```

Upgrade immutable image references and review rendered changes before applying. Database engine upgrades require their own supported migration procedure. A Helm rollback changes manifests and image versions; it does not reverse database schema changes, restore deleted orders, or revert PVC contents.

For the local acceptance scenario, `uv run python scripts/verify_release.py` records non-sensitive user/product/order IDs and PVC identities, restarts WordPress and both database pods, and checks that those records persist. It then changes only the gateway CPU limit through a new Helm revision, rolls back to the compatible revision it started from, and checks the original configuration and records. This deliberately uses unchanged image tags and proves configuration rollback, not old application-binary or database recovery. Run it only while other clients are idle.

```sh
helm history commerce -n commerce
helm upgrade commerce deploy/chart -n commerce -f /secure/path/values-prod.yaml --wait --timeout 15m
# Choose an actually compatible revision from history after reviewing migrations.
helm rollback commerce PREVIOUS_REVISION -n commerce --wait --timeout 15m
```

For a data restore, use a separate recovery namespace first. Restore the saved Secrets before starting compatible database/application versions with fresh, deliberately empty PVCs. The Windows backup's `kubernetes-secrets.json` strips server-generated metadata; adjust its namespace for the recovery environment before applying it. For a raw Linux export, also remove resource versions, UIDs and timestamps. Restore `development.json` and `gemini-api-key.txt` privately before subsequent local bootstrap. Keep the gateway, Woo upstream and WordPress writers stopped while importing. Restore PostgreSQL with `pg_restore -U commerce -d commerce --clean --if-exists`, feed the SQL dump to `mariadb --user=wordpress --password="$MARIADB_PASSWORD" wordpress`, and extract the WordPress archive into its mounted PVC using a temporary recovery pod with UID/GID 33. These commands replace data and therefore are not part of automatic setup. Validate record counts, login, checkout and MCP calls before switching traffic. Keep the original volumes until recovery has been verified.

PVCs have `helm.sh/resource-policy: keep`. `uninstall.ps1` leaves them and both external Secrets in place. Deleting the k3d cluster or its Docker volumes is a separate destructive action and is intentionally not included in the teardown helper.

## Verified local results

The following checks passed on 15 October 2026 against the running local cluster. The helpers write non-sensitive evidence to `.runtime/*-verification.json`.

| Check | Observed result |
| --- | --- |
| `scripts/verify_network.py` | Gateway connections succeeded; peer and untrusted pods were denied protected Services. The gateway could get/patch only its named credential Secret and could not update it, read the application Secret, list Secrets, or create deployments. |
| `scripts/verify_registry_lifecycle.py` | Deleted shipping registration remained absent after gateway and upstream replacement. The upstream started using its separate service token; the registration and original read permissions were restored. |
| `scripts/verify_failure.py` | Shipping was held at zero replicas and reported Unreachable. Discovery took 0.015 seconds, policy/Woo calls succeeded, the failed tool returned an error, and a replacement pod recovered. Its temporary API key was revoked. |
| `scripts/verify_release.py` | Replacement WordPress, MariaDB and PostgreSQL pods retained the same PVC identities, three user records, four products, order 18, and the demo notice content. Revision 4 changed only a gateway CPU limit; revision 5 rolled back to compatible revision 3 and restored the limit. This was a configuration rollback with unchanged image tags. |

After verification, all eight deployments were ready, all three registered connections were Connected, and the API, anonymous product catalog and linked demo data-use page returned HTTP 200. K3s reported Secret encryption enabled, `reencrypt_finished`, and matching encryption hashes. No public VPS or old-binary/database restore is claimed by these checks.

## Troubleshooting

Use `stop.ps1` and `bootstrap.ps1` for the normal local lifecycle. Resuming an existing cluster now checks the host API with bounded retries and refreshes only the project load balancer if the API is healthy inside the node but unavailable on the host. Deployment then recomputes the narrow API-server egress CIDR. Missing local credentials with retained project Secrets or volumes stop bootstrap instead of silently generating new database passwords. The isolated recovery and credential-guard cases can be run with `pwsh -File scripts/test-local-recovery.ps1`.

A manual `docker restart` of only the k3d server can change its Docker-network IP while the project load balancer still caches the old address. For that specific condition outside normal bootstrap, restart only `k3d-urumi-commerce-serverlb`, wait for its proxy to start, then run `deploy.ps1`. Node readiness alone does not prove upstream credentials are reachable. Do not restart Docker Desktop or delete other containers as a workaround.

```powershell
docker exec k3d-urumi-commerce-server-0 kubectl get --raw /readyz
# Only when diagnosing that specific stale proxy condition:
docker restart k3d-urumi-commerce-serverlb
./scripts/deploy.ps1
```

On a 16 GiB Windows host, serialize image builds and browser runs. Severe host memory pressure can delay the whole Docker VM even when individual workload limits are respected. The probes allow 3 to 5 seconds and require sustained liveness failures before restarting; they do not make an overloaded host reliable.

```powershell
kubectl -n commerce get pods,pvc,ingress
kubectl -n commerce get events --sort-by=.metadata.creationTimestamp
kubectl -n commerce logs deployment/gateway --tail=80
kubectl -n commerce logs job/wordpress-bootstrap --tail=80
```

An upstream can report Connected while the WooCommerce REST call itself fails; MCP service health and store health are separate boundaries. Tool errors surface that failure and are audited. A live pod with an unreachable dependency should not trigger a restart cascade. Check the dashboard's last-error field, the relevant workload logs, namespace policies and readiness before changing credentials. Do not paste Secret contents into issues or screenshots.
