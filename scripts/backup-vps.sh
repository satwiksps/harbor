#!/usr/bin/env bash
# Application backup on the k3s VPS. Pause writes before running this helper.
set -euo pipefail
umask 077

if [[ $EUID -ne 0 || $# -gt 1 ]]; then
  echo 'Usage: sudo bash scripts/backup-vps.sh [NEW_BACKUP_DIRECTORY]' >&2
  exit 2
fi
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
test -r "$KUBECONFIG"
cd "$(dirname "$0")/.."
backup=${1:-"$PWD/.secrets/backups/$(date -u +%Y%m%d-%H%M%S)"}
mkdir -p "$(dirname "$backup")"
# Never mix an earlier complete snapshot with a partially failed new one.
mkdir -m 700 "$backup"
save() {
  local filename=$1
  shift
  "$@" > "$backup/$filename.partial"
  mv "$backup/$filename.partial" "$backup/$filename"
}
kubectl -n harbor get secret harbor-app-secrets harbor-upstream-credentials -o json |
  python3 -c '
import json, sys
document = json.load(sys.stdin)
for item in document["items"]:
    item["metadata"] = {key: item["metadata"][key] for key in ("name", "namespace")}
json.dump(document, sys.stdout, indent=2)
' \
  > "$backup/kubernetes-secrets.json.partial"
mv "$backup/kubernetes-secrets.json.partial" "$backup/kubernetes-secrets.json"
save gateway.dump kubectl -n harbor exec deployment/postgres -- pg_dump -U harbor -d harbor -Fc
save wordpress.sql kubectl -n harbor exec deployment/mariadb -- sh -c 'mariadb-dump --single-transaction --user=wordpress --password="$MARIADB_PASSWORD" wordpress'
save wordpress-content.tar.gz kubectl -n harbor exec deployment/wordpress -- tar -czf - -C /var/www/html wp-content wp-config.php
save helm-values.yaml helm get values harbor -n harbor --all
for filename in development.json gemini-api-key.txt; do
  if [[ -f .secrets/$filename ]]; then cp ".secrets/$filename" "$backup/$filename"; fi
done
(cd "$backup" && sha256sum ./* > SHA256SUMS)
printf 'Private application backup: %s\nCopy it off the VM before the trial ends.\n' "$backup"
