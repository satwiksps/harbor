#!/usr/bin/env bash
# Run on the single k3s VPS after importing the three versioned application images.
set -euo pipefail
umask 077

if [[ $# -ne 4 || $EUID -ne 0 ]]; then
  echo 'Usage: sudo bash scripts/deploy-vps.sh CONSOLE_HOST STORE_HOST ADMIN_EMAIL IMAGE_TAG' >&2
  exit 2
fi
console_host=$1
store_host=$2
admin_email=$3
image_tag=$4
for host in "$console_host" "$store_host"; do
  if [[ ! $host =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ || $host != *.* ]]; then
    echo 'Supply DNS hostnames without a scheme, port, or path.' >&2
    exit 2
  fi
done
if [[ $console_host == "$store_host" || ! $image_tag =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]]; then
  echo 'Use separate console/store hosts and a valid immutable image tag.' >&2
  exit 2
fi
for command in kubectl k3s helm python3; do command -v "$command" >/dev/null; done
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
test -r "$KUBECONFIG"
cd "$(dirname "$0")/.."
mkdir -p .runtime
kubectl -n harbor get ingress harbor --ignore-not-found -o json > .runtime/vps-ingress.json
python3 - "$console_host" "$store_host" <<'PY'
import json
import re
import sys
from pathlib import Path

console, store = sys.argv[1:]
raw = Path('.runtime/vps-ingress.json').read_text().strip()
tls = [{'secretName': 'harbor-ingress-tls', 'hosts': [console, store]}]
if raw:
    spec = json.loads(raw)['spec']
    routes = {
        rule.get('host'): {path['backend']['service']['name'] for path in rule.get('http', {}).get('paths', [])}
        for rule in spec.get('rules', [])
    }
    if routes != {console: {'gateway', 'dashboard'}, store: {'wordpress'}}:
        raise SystemExit('Existing ingress hosts or service roles differ. Use a controlled domain migration before deploying; no resources or credentials were changed.')
    tls = spec.get('tls')
    if not isinstance(tls, list) or not tls:
        raise SystemExit('Existing ingress has no TLS configuration. Restore its certificates before deploying.')
    covered = set()
    for entry in tls:
        if not isinstance(entry, dict):
            raise SystemExit('Existing ingress has an invalid TLS entry.')
        name, hosts = entry.get('secretName'), entry.get('hosts')
        if not isinstance(name, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', name):
            raise SystemExit('Existing ingress has an invalid TLS Secret name.')
        if not isinstance(hosts, list) or not hosts or any(not isinstance(host, str) or not host for host in hosts):
            raise SystemExit('Existing ingress has invalid TLS hostnames.')
        covered.update(hosts)
    if not {console, store}.issubset(covered):
        raise SystemExit('Existing ingress TLS does not cover both requested hosts. Complete the domain migration before deploying.')
Path('.runtime/vps-tls.json').write_text(json.dumps(tls) + '\n')
PY
mkdir -p .secrets
chmod 700 .secrets
kubectl get nodes -o json > .runtime/vps-nodes.json
python3 - <<'PY'
import json
nodes = json.load(open('.runtime/vps-nodes.json'))['items']
if len(nodes) != 1 or not any(c['type'] == 'Ready' and c['status'] == 'True' for c in nodes[0]['status']['conditions']):
    raise SystemExit('This helper requires one ready k3s node.')
PY
kubectl -n cert-manager rollout status deployment/cert-manager --timeout=180s
for name in harbor harbor-dashboard harbor-wordpress; do
  if ! k3s ctr images list -q | grep -Fx "docker.io/library/$name:$image_tag" >/dev/null; then
    echo "Import $name:$image_tag into k3s before deployment." >&2
    exit 1
  fi
done
kubectl create namespace harbor --dry-run=client -o yaml | kubectl apply -f -
if [[ ! -f .secrets/development.json ]]; then
  existing=$(kubectl -n harbor get secret/harbor-app-secrets secret/harbor-upstream-credentials pvc/wordpress-data pvc/mariadb-data pvc/postgres-data --ignore-not-found -o name)
  if [[ -n $existing ]]; then
    echo 'Existing Secrets or data volumes found. Restore the matching private credential file before deploying.' >&2
    exit 1
  fi
fi
python3 scripts/prepare-secrets.py --namespace harbor --admin-email "$admin_email"
python3 - "$console_host" "$store_host" "$admin_email" "$image_tag" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

console, store, email, tag = sys.argv[1:]
credentials = json.loads(Path('.secrets/development.json').read_text())
if credentials['admin_email'] != email:
    raise SystemExit('Administrator email differs from the retained credentials. Use the existing identity.')
node = json.loads(Path('.runtime/vps-nodes.json').read_text())['items'][0]
internal = next(a['address'] for a in node['status']['addresses'] if a['type'] == 'InternalIP')
pod_cidrs = node['spec'].get('podCIDRs') or [node['spec']['podCIDR']]
values = {
    'images': {'app': f'harbor:{tag}', 'dashboard': f'harbor-dashboard:{tag}', 'wordpress': f'harbor-wordpress:{tag}'},
    'rolloutRevision': datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S'),
    'ingress': {
        'consoleHost': console, 'storeHost': store,
        'annotations': {'cert-manager.io/issuer': 'harbor-letsencrypt', 'traefik.ingress.kubernetes.io/router.entrypoints': 'websecure'},
        'tls': json.loads(Path('.runtime/vps-tls.json').read_text()),
    },
    'urls': {'console': f'https://{console}', 'store': f'https://{store}'},
    'gateway': {'adminEmail': email, 'trustedProxyCidrs': pod_cidrs, 'secureCookies': True},
    'wordpress': {'adminUser': credentials['wordpress_admin_user']},
    'networkPolicy': {'allowAcmeHttp01': True, 'apiServerNodeCidr': internal + '/32'},
}
Path('.runtime/values-vps.json').write_text(json.dumps(values, indent=2) + '\n')
PY
api_ip=$(kubectl -n default get service kubernetes -o jsonpath='{.spec.clusterIP}')
if [[ -n $(kubectl -n harbor get secret harbor-app-secrets --ignore-not-found -o name) ]]; then
  kubectl -n harbor get secret harbor-app-secrets -o json | python3 -c '
import base64, json, sys
from pathlib import Path
actual = json.load(sys.stdin)["data"]
expected = json.loads(Path(".secrets/k8s-app-secret.json").read_text())["stringData"]
if any(base64.b64decode(actual.get(key, "")).decode() != value for key, value in expected.items()):
    raise SystemExit("Retained application credentials differ from the deployed Secret. Reconcile the protected files and rotate deliberately before deploying.")
'
else
  kubectl -n harbor create -f .secrets/k8s-app-secret.json
fi
if [[ -z $(kubectl -n harbor get secret harbor-upstream-credentials --ignore-not-found -o name) ]]; then
  kubectl -n harbor create -f .secrets/k8s-upstream-secret.json
fi
kubectl apply -f deploy/vps/traefik.yaml -f deploy/vps/tls.yaml
helm lint deploy/chart -f deploy/values-prod.yaml -f .runtime/values-vps.json --set-string "networkPolicy.apiServerCidr=$api_ip/32"
helm upgrade --install harbor deploy/chart -n harbor -f deploy/values-prod.yaml -f .runtime/values-vps.json \
  --set-string "networkPolicy.apiServerCidr=$api_ip/32" --wait --timeout 15m
kubectl -n harbor wait --for=condition=complete job/wordpress-bootstrap --timeout=600s
certificates=$(python3 -c 'import json; print("\n".join(dict.fromkeys(item["secretName"] for item in json.load(open(".runtime/values-vps.json"))["ingress"]["tls"])))')
while IFS= read -r certificate; do
  kubectl -n harbor wait --for=condition=Ready "certificate/$certificate" --timeout=600s
done <<< "$certificates"
kubectl -n harbor get deployments,pvc,certificate
printf 'Deployed https://%s and https://%s. Run the public acceptance checks before sharing.\n' "$console_host" "$store_host"
