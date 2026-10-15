# Demo walkthrough

Aim for a focused 10 to 12 minute recording. Use the real Kubernetes deployment, two browser profiles, a member MCP client, and a terminal. Do a rehearsal first. Narrate decisions in your own words; the interview will cover the implementation.

## Prepare

- Start the cluster using [getting started](getting-started.md), then run the relevant checks in [development](development.md).
- Sign in as the administrator in the first browser and the demo member in a separate browser profile. Generate the external-client key before recording, and keep the key dialog and private files out of frame.
- Configure the MCP client using the member's URL and key. Test that all seven read tools appear, including the two distinct `search` tools.
- Choose a unique product name and SKU for this recording. Have a fictional Indian checkout address ready. Set browser zoom to 100 percent.
- Open Connections, Activity, storefront, WordPress Orders, and a terminal with namespace `commerce`. Keep passwords, Secrets and unredacted client configuration closed.

## Show, then explain

1. **Architecture, one minute.** Show the diagram in the design note. The browser talks to the gateway API; chat and external clients use the same MCP endpoint. Only the gateway reaches the three internal MCP servers. The commerce server signs requests to WooCommerce REST. WordPress, MariaDB and PostgreSQL have persistent volumes.
2. **Admin chat and checkout, three minutes.** Ask chat to create your unique product at a specific price. Expand the proposed write arguments, approve, and show the successful tool result. Open its storefront page, add it to the cart and complete COD checkout. Show the order in WordPress admin. Ask chat for today's orders and point to this order and its tool call.
3. **Member and two upstreams, two minutes.** Switch to the member's external client. Show the tool names and that create/update tools are absent. Ask today's orders. Run the shipping-policy-plus-quote prompt from the [user guide](user-guide.md#connect-an-external-client). Show both upstream tool results. Explain that the external client has only a gateway URL and one personal key.
4. **Control and isolation, two minutes.** Disable Shipping in Connections, refresh external discovery and show its tools disappear. Read the returns policy successfully. Re-enable Shipping. Revoke the member's key and show that the next request is rejected. Show Activity with both users and success/denied outcomes.
5. **Failure and Kubernetes, two minutes.** Hold one upstream down with `kubectl -n commerce scale deployment/policies-mcp --replicas=0`, then wait for the dashboard to show Unreachable. Call a commerce or shipping tool successfully. Restore with `--replicas=1`. Scaling to zero holds the failure long enough to observe it; Kubernetes normally replaces a killed pod quickly. Also show a pod deletion and its replacement. Show pods, PVCs, Ingress and the RBAC/NetworkPolicy manifests, without displaying Secret data.
6. **Production and tradeoffs, one minute.** Compare local and production values. Explain DNS, TLS, images, storage and cluster Secret encryption. Describe backup before upgrade and why Helm rollback does not restore database contents. State which bonuses are complete and what is deliberately omitted.

Useful terminal commands:

```powershell
kubectl --context k3d-urumi-commerce -n commerce get pods,svc,ingress,pvc
kubectl --context k3d-urumi-commerce -n commerce get networkpolicy
kubectl --context k3d-urumi-commerce -n commerce get role,rolebinding
.tools/helm.exe history commerce -n commerce
```

Do not run `kubectl get secret -o yaml` during a recording. Do not show `.secrets`, raw request headers, or generated key dialogs.

## Explain these details confidently

- Tool aliases combine the upstream slug, sanitized tool name and a stable hash. The real two-server `search` collision demonstrates the scheme.
- Tool listing reads a cached catalog. A failed discovery does not block every client; each invocation rechecks identity, permission and enabled state.
- A member key hashes to one user. The gateway then loads a separate upstream credential. The client's token is never forwarded.
- Chat gets a short-lived, session-bound internal delegation token. Actual execution goes back through the MCP HTTP endpoint, so the same checks and audit apply.
- Prompt instructions are not an authorization system. Hidden tools, direct-call checks, schema validation and session-bound write confirmations enforce the boundary.
- The network policy and URL validator solve different problems. The former controls pod traffic; the latter constrains administrator-registered destinations and blocks DNS rebinding at connection time.
- Shared store credentials still grant powerful authority to an authorized member. Read access includes commercial information, so order output excludes customer contact details.
- A write timeout can be ambiguous. Do not retry a product creation automatically; check store state first. Use a unique SKU when possible.

## Submission checklist

- Repository accessible to reviewers, with no private credentials or generated runtime artifacts.
- Video link accessible to anyone with the link, reviewed for accidental secrets.
- Both users and every required flow visibly demonstrated.
- Multi-store bonus: No. Stand-out items: tool permissions and security hardening. Claim additional items only if demonstrated and explained.
- LLM: Google Gemini, model configured in Helm values. The current local default is `gemini-3.1-flash-lite`.
- Leave the live deployment URL empty unless you actually deploy and verify a public server.

The submission form is linked in the original assignment. Do not submit a placeholder repository or video URL.
