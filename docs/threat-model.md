# Threat model

## Assets and actors

Protected assets are upstream credentials, the LLM key, dashboard sessions, API keys, customer order data and store mutations. Actors are an administrator, a member, an unauthenticated network client, an untrusted MCP server and a compromised pod. Cluster administrators are outside the application isolation boundary.

| Threat | Control | Remaining limitation |
| --- | --- | --- |
| Member invokes an administrator tool by name | Same permission predicate filters listing and checks execution | Admins intentionally have access to all registered tools |
| Revoked key continues on an open MCP connection | Database authentication on each request, including delegated sessions | Already dispatched operations may finish |
| One member reads another member's records or confirmation | Audit query ownership and session-bound one-use confirmations | Members intentionally share permitted store data |
| Tool target or permissions change while chat waits | Target-bound calls and confirmations; current authority checked again before dispatch; automatic chat calls must remain read-only | A change after dispatch cannot undo an upstream operation |
| External client uses an administrator key for an unwanted write | Use member keys for clients that should only read; every call checks current role and tool permissions | Dashboard approval dialogs do not apply to direct administrator MCP calls |
| Untrusted schema retrieves data or exhausts resources | Local references only, empty retrieval registry, bounded input, killable worker with a two-second execution deadline and Linux address-space limit; process startup has a separate five-second readiness limit | Both limits remain inside the outer tool/discovery deadline. Windows has deadline/input guards; memory-budget verification uses Linux containers |
| Upstream steals a client credential | User keys and cookies are never forwarded upstream; only that upstream's configured credential is attached | An upstream sees its own credential and authorized arguments |
| Server registration targets metadata or internal systems | Public HTTPS validation, exact internal allowlist, DNS validation at connect time, numeric-address connection and blocked redirects | Operator-approved internal hosts are trusted; gateway compromise bypasses application checks |
| Upstream tool description manipulates the LLM | Descriptions/results treated as untrusted; role enforcement outside model; write confirmation; no secrets in model context | Prompt injection cannot be eliminated; an authorized user can still approve a bad action |
| Slow upstream consumes gateway capacity | Bounded discovery, per-upstream concurrency, deadlines and result limits | A single-node system still has finite shared CPU and memory |
| Tool arguments or result contain HTML/script | React escapes displayed text; no arbitrary upstream HTML rendering | External clients control their own rendering behavior |
| Password guessing or abusive traffic | Argon2 passwords, two shared hashing slots, synchronized rate limits using trusted ingress client identity, request size limits | Limits are process-local; NAT clients may share an address; this is not a DDoS protection service |
| Secret leaks via repo, Helm values, browser or logs | Ignored local secret files, referenced Kubernetes Secrets, raw and JSON-escaped credential redaction, write-only credential UI; invitation query strings excluded from access logs and referrers | Host owner and cluster administrators can read secrets |
| Gateway pod obtains cluster control | Service account can only read/patch one named credential Secret | Gateway compromise still exposes all credentials in that Secret |
| Pod bypasses gateway | Internal-only services, bearer credentials and enforced NetworkPolicies | Node compromise or cluster-admin privileges defeat pod isolation |
| Duplicate write after a lost response | No automatic retry, explicit uncertain-outcome error, optional unique SKU reconciliation | No distributed exactly-once guarantee |

## Shared credential implications

A member can read every store record permitted by the gateway even though they never see the store's credential. Hiding a credential alone is not authorization. Tool policies limit what the gateway will execute. Unknown tools default to administrator-only until reviewed. The demo policy permits members to read products and orders and use policies and shipping estimates. Only admins can create products or update orders.

Gateway keys identify users at the gateway only. Passing them through would expose a reusable team credential to every upstream and blur which service is responsible for authorization. An upstream instead receives its own separately configured static credential. The audit log records the originating user before the upstream call.

## Operational boundaries

The sample store contains synthetic data. Free LLM provider terms may allow submitted content to improve products. Production operators must evaluate provider terms and minimize customer data before using a free tier with real customers.

Local HTTP is limited to development. Production values require TLS at ingress and secure cookies. Internal HTTP and database links are not encrypted by a service mesh. Kubernetes Secret encryption protects datastore contents at rest, not credentials read by a running authorized process.

Keep dependencies pinned, restrict public registration of untrusted servers, review newly discovered tools, rotate credentials after suspected compromise and back up all persistent data. Do not expose local test fixtures or use the local values on a public server unchanged.
