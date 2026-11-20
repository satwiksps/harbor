#!/usr/bin/env bash
# Scheduled off-node backup for the k3s VPS: run backup-vps.sh, upload one
# archive to S3 and keep only the newest local copies. Configuration comes
# from /etc/harbor/backup.env through deploy/vps/harbor-backup.service.
set -euo pipefail
umask 077

if [[ $EUID -ne 0 || $# -ne 0 ]]; then
  echo 'Usage: sudo HARBOR_REPO=... HARBOR_BACKUP_BUCKET=... HARBOR_BACKUP_REGION=... bash scripts/backup-offsite.sh' >&2
  exit 2
fi
: "${HARBOR_REPO:?Set HARBOR_REPO to the Harbor checkout.}"
: "${HARBOR_BACKUP_BUCKET:?Set HARBOR_BACKUP_BUCKET.}"
: "${HARBOR_BACKUP_REGION:?Set HARBOR_BACKUP_REGION.}"
keep=${HARBOR_BACKUP_KEEP_LOCAL:-7}
if [[ ! $keep =~ ^[1-9][0-9]*$ ]]; then
  echo 'HARBOR_BACKUP_KEEP_LOCAL must be a positive integer.' >&2
  exit 2
fi

stamp=$(date -u +%Y%m%d-%H%M%S)
nightly=$HARBOR_REPO/.secrets/backups/nightly
archive=$(mktemp --suffix=.tar.gz /var/tmp/harbor-backup-XXXXXX)
trap 'rm -f "$archive"' EXIT

bash "$HARBOR_REPO/scripts/backup-vps.sh" "$nightly/$stamp"
tar -czf "$archive" -C "$nightly" "$stamp"
aws s3 cp --only-show-errors --region "$HARBOR_BACKUP_REGION" \
  "$archive" "s3://$HARBOR_BACKUP_BUCKET/nightly/$stamp.tar.gz"

# Only scheduled copies are pruned; manual backups in .secrets/backups stay.
find "$nightly" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort | head -n "-$keep" |
  while IFS= read -r old; do rm -rf -- "${nightly:?}/$old"; done
echo "Uploaded s3://$HARBOR_BACKUP_BUCKET/nightly/$stamp.tar.gz ($(du -h "$archive" | cut -f1))"
