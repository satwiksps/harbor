# Security model

Harbor protects dashboard sessions, API keys, upstream credentials, the model-provider key and store data. Its main boundary is between a member and an administrator. It also treats registered MCP servers and their tool descriptions, schemas and results as untrusted input.

Cluster administrators and the host owner are trusted operators. A compromised gateway process can access the upstream credentials assigned to it. Harbor is designed for one team sharing one store, not mutually isolated tenants.

## Identity and permissions

Members can use tools explicitly classified as read operations. Administrators can use all registered tools and manage connections, permissions and accounts. Unknown tools default to administrator-only until reviewed.

The same permission check filters MCP discovery and rejects direct calls. Hiding a button or tool name is not the authorization boundary. Database authentication runs on each request, including requests on an existing transport, so revoked keys and role changes take effect on the next request. An operation already dispatched may still finish.

Members see their own Activity and keys. They can read any store records exposed by their permitted tools; Harbor does not provide per-customer or per-order access rules.

Dashboard sessions use opaque tokens stored as hashes, HttpOnly cookies, origin checks and CSRF protection. Passwords use Argon2. API keys have sufficient random entropy for SHA-256 storage and are shown in full only when created.

## Chat and write approval

The model chooses tools; the gateway independently authorizes execution. Dashboard chat pauses mutations for a one-use, session-bound confirmation. A proposal binds the intended tool to its connection, schema and permissions. Those values are checked again before dispatch. Automatic chat calls must still be read-only when execution begins.

External administrator clients can invoke write tools directly. The dashboard confirmation dialog does not apply to their calls. Use member keys for external clients that should only read.

Untrusted descriptions or results may influence a model. Role checks, confirmation and credential isolation limit the consequences, but cannot prevent a user from approving an unwanted authorized action. Neither the model nor the browser receives upstream credentials.

Assistant Markdown disables raw HTML, rejects unsafe link schemes and renders remote images as links rather than fetching them. User messages and displayed tool data remain escaped text. External clients are responsible for their own rendering.

## Registered servers and network access

Public upstreams must use HTTPS. URL validation blocks private, loopback, link-local and cloud metadata destinations except exact operator-configured internal hosts. DNS is checked at connection time, connections use the validated numeric address and redirects are blocked.

Each upstream receives only its configured server credential. User API keys and session cookies are never forwarded. An upstream can see its own credential and the arguments of authorized calls, so register only services appropriate for that data.

Kubernetes Services for upstreams and databases are internal. Default-deny NetworkPolicies allow only the required paths, and bundled upstreams also require bearer authentication. These controls require an enforcing network-policy controller; node or cluster-admin compromise defeats pod isolation.

## Resource limits and failures

Tool and discovery calls have deadlines, output limits and per-upstream concurrency limits. Untrusted schemas accept only in-document references and run in a killable worker. Validation has a two-second execution deadline, a separate five-second process readiness limit, structural input limits and a Linux memory limit. Both deadlines remain within the enclosing operation's timeout.

Password hashing has two shared, non-queueing slots. Excess requests receive a temporary capacity error instead of allocating another Argon2 operation. Authentication and gateway traffic also have process-local rate limits. Trusted ingress configuration prevents clients from choosing an arbitrary source address through forwarded headers. Users behind the same NAT can still share a bucket.

These controls bound ordinary application work; they do not provide distributed rate limiting, high availability or DDoS protection.

Writes are not automatically retried. A response can be lost after the store commits an operation. Reconcile the store state before retrying; a unique SKU helps identify a product creation but does not guarantee exactly-once delivery.

## Secrets and audit data

The gateway's Kubernetes Role can get and patch one named mutable credential Secret. It cannot list other Secrets or create workloads. Bundled upstreams read separate service tokens from the application Secret, so deleting a registry credential does not prevent an upstream pod from starting.

Helm references existing Secrets instead of embedding values in releases. K3s Secret encryption protects datastore files at rest. It does not hide values from a running authorized process or an operator with the node's keys.

Audit records identify the originating user, server, tool, duration and outcome without storing raw customer order payloads. Credential redaction covers raw and JSON-escaped values; invitation query strings are excluded from access logs and referrers. Local credentials, backups and runtime reports must remain outside source control.

## Deployment boundaries

Use TLS and secure cookies for public access. Local HTTP is for development. Internal HTTP and database traffic are not encrypted by a service mesh.

The bundled store starts with synthetic products and sample policy/shipping rules. Before handling real customers, configure business policies and evaluate what data is sent to the model provider. Treat prompts and tool results as data disclosed to that provider.

Keep dependencies locked, review newly discovered tools, rotate exposed credentials and back up databases, WordPress content and the corresponding Secrets together. The [operations guide](operations.md#boundaries-and-secret-storage) covers network configuration, credential rotation and recovery.
