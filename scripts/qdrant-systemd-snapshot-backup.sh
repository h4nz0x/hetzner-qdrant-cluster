#!/usr/bin/env bash
set -euo pipefail

env_file="${QDRANT_BACKUP_ENV_FILE:-/etc/qdrant-backup/backup.env}"
state_dir="${QDRANT_BACKUP_STATE_DIR:-/var/lib/qdrant-backup}"
lib_dir="${QDRANT_BACKUP_LIB_DIR:-/usr/local/lib/qdrant-backup}"
ssh_config="${QDRANT_BACKUP_SSH_CONFIG:-/etc/qdrant-backup/ssh_config}"
node_script="${lib_dir}/qdrant-backup-node.sh"
manifest_script="${lib_dir}/qdrant-backup-manifest.py"
retention_script="${lib_dir}/qdrant-s3-retention-plan.py"
remote_cmd='/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /bin/bash -s --'
nodes=(qdrant-node-1 qdrant-node-2 qdrant-node-3)
host_ips=(203.0.113.11 203.0.113.12 203.0.113.13)

if [[ -r "$env_file" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$env_file"
  set +a
fi

required_vars=(
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_DEFAULT_REGION
  QDRANT_BACKUP_S3_BUCKET
  QDRANT_BACKUP_S3_PREFIX
)

for name in "${required_vars[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    printf '%s is not configured\n' "$name" >&2
    exit 1
  fi
done

for command in aws date mktemp python3 ssh ssh-keygen ssh-keyscan timeout; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'missing required command: %s\n' "$command" >&2
    exit 2
  }
done

for file in "$node_script" "$manifest_script" "$retention_script" "$ssh_config"; do
  if [[ ! -r "$file" ]]; then
    printf 'required backup file is missing or unreadable: %s\n' "$file" >&2
    exit 2
  fi
done

install -d -m 0700 "$state_dir"
run_dir="$(mktemp -d "${state_dir}/run.XXXXXXXX")"
raw_dir="${run_dir}/raw"
report_dir="${run_dir}/report"
known_hosts_file="${run_dir}/known_hosts"
latest_dir="${state_dir}/latest"
install -d -m 0700 "$raw_dir" "$report_dir"
install -m 0600 /dev/null "$known_hosts_file"

cleanup() {
  rm -rf "$run_dir"
}
trap cleanup EXIT

ssh-keyscan -T 10 -t ed25519 "${host_ips[@]}" 2>/dev/null >> "$known_hosts_file"
for host in "${host_ips[@]}"; do
  ssh-keygen -F "$host" -f "$known_hosts_file" >/dev/null || {
    printf 'could not obtain SSH host key for %s\n' "$host" >&2
    exit 1
  }
done

backup_id="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf '%s\n' "$backup_id" > "${report_dir}/backup-id.txt"

for node in "${nodes[@]}"; do
  timeout 900 ssh \
    -F "$ssh_config" \
    -o "UserKnownHostsFile=${known_hosts_file}" \
    "$node" \
    "${remote_cmd} create ${node} ${backup_id}" \
    < "$node_script" \
    > "${raw_dir}/${node}.json"
done

python3 "$manifest_script" \
  --node-json "${raw_dir}/qdrant-node-1.json" \
  --node-json "${raw_dir}/qdrant-node-2.json" \
  --node-json "${raw_dir}/qdrant-node-3.json" \
  --backup-id "$backup_id" \
  --bucket "$QDRANT_BACKUP_S3_BUCKET" \
  --prefix "$QDRANT_BACKUP_S3_PREFIX" \
  --output-json "${report_dir}/qdrant-backup-manifest.json" \
  --output-md "${report_dir}/qdrant-backup-summary.md" \
  --upload-tsv "${report_dir}/qdrant-backup-upload.tsv"

while IFS=$'\t' read -r node collection snapshot_name s3_key; do
  timeout 900 ssh \
    -F "$ssh_config" \
    -o "UserKnownHostsFile=${known_hosts_file}" \
    "$node" \
    "${remote_cmd} download ${collection} ${snapshot_name}" \
    < "$node_script" \
    | aws s3 cp - "s3://${QDRANT_BACKUP_S3_BUCKET}/${s3_key}"
done < "${report_dir}/qdrant-backup-upload.tsv"

manifest_file="${report_dir}/qdrant-backup-manifest.json"
manifest_key="$(
  python3 - "$manifest_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["manifest_key"])
PY
)"
aws s3 cp "$manifest_file" "s3://${QDRANT_BACKUP_S3_BUCKET}/${manifest_key}"

prefix="${QDRANT_BACKUP_S3_PREFIX%/}/"
aws s3api list-objects-v2 \
  --bucket "$QDRANT_BACKUP_S3_BUCKET" \
  --prefix "$prefix" \
  --delimiter "/" \
  > "${report_dir}/s3-prefixes.json"

python3 "$retention_script" \
  --prefixes-json "${report_dir}/s3-prefixes.json" \
  --keep "${QDRANT_BACKUP_RETENTION_KEEP:-2}" \
  --protected-backup-id "$backup_id" \
  --output "${report_dir}/delete-prefixes.txt"

while IFS= read -r delete_prefix; do
  [[ -z "$delete_prefix" ]] && continue
  aws s3 rm "s3://${QDRANT_BACKUP_S3_BUCKET}/${delete_prefix}" --recursive
done < "${report_dir}/delete-prefixes.txt"

rm -rf "$latest_dir"
install -d -m 0750 "$latest_dir"
cp "${report_dir}/qdrant-backup-manifest.json" "$latest_dir/"
cp "${report_dir}/qdrant-backup-summary.md" "$latest_dir/"
cp "${report_dir}/delete-prefixes.txt" "$latest_dir/"
chmod 0640 "$latest_dir"/*

cat "${report_dir}/qdrant-backup-summary.md"
