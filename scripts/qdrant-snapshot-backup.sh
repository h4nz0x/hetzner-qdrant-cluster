#!/usr/bin/env bash
set -euo pipefail

required=(
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY
  AWS_DEFAULT_REGION
  QDRANT_BACKUP_S3_BUCKET
  QDRANT_BACKUP_S3_PREFIX
  QDRANT_SSH_PRIVATE_KEY
  RUNNER_TEMP
)

for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "::error::${name} is not configured"
    exit 1
  fi
done

ssh_dir="${RUNNER_TEMP}/production-qdrant-backup-ssh"
key_file="${ssh_dir}/qdrant_backup"
known_hosts_file="${ssh_dir}/known_hosts"
config_file="${ssh_dir}/config"
raw_dir="${RUNNER_TEMP}/production-qdrant-backup-raw"
report_dir="${RUNNER_TEMP}/production-qdrant-backup-report"
remote_cmd='/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin /bin/bash -s --'

install -d -m 0700 "$ssh_dir" "$raw_dir" "$report_dir"
printf '%s\n' "$QDRANT_SSH_PRIVATE_KEY" > "$key_file"
chmod 0600 "$key_file"
install -m 0600 /dev/null "$known_hosts_file"

cat > "$config_file" <<EOF
Host qdrant-node-1
  HostName 203.0.113.11
  User root
  IdentityFile ${key_file}
  IdentitiesOnly yes
  UserKnownHostsFile ${known_hosts_file}
  StrictHostKeyChecking yes
  ControlMaster no
  ControlPath none
  ServerAliveInterval 15
  ServerAliveCountMax 3

Host qdrant-node-2
  HostName 203.0.113.12
  User root
  IdentityFile ${key_file}
  IdentitiesOnly yes
  UserKnownHostsFile ${known_hosts_file}
  StrictHostKeyChecking yes
  ControlMaster no
  ControlPath none
  ServerAliveInterval 15
  ServerAliveCountMax 3

Host qdrant-node-3
  HostName 203.0.113.13
  User root
  IdentityFile ${key_file}
  IdentitiesOnly yes
  UserKnownHostsFile ${known_hosts_file}
  StrictHostKeyChecking yes
  ControlMaster no
  ControlPath none
  ServerAliveInterval 15
  ServerAliveCountMax 3
EOF
chmod 0600 "$config_file"

ssh-keyscan -T 10 -t ed25519 \
  203.0.113.11 203.0.113.12 203.0.113.13 \
  2>/dev/null >> "$known_hosts_file"
for host in 203.0.113.11 203.0.113.12 203.0.113.13; do
  ssh-keygen -F "$host" -f "$known_hosts_file" >/dev/null || {
    echo "::error::Could not obtain SSH host key for ${host}"
    exit 1
  }
done

backup_id="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf '%s\n' "$backup_id" > "${report_dir}/backup-id.txt"

for node in qdrant-node-1 qdrant-node-2 qdrant-node-3; do
  timeout 900 ssh \
    -F "$config_file" \
    "$node" \
    "${remote_cmd} create ${node} ${backup_id}" \
    < scripts/qdrant-backup-node.sh \
    > "${raw_dir}/${node}.json"
done

python3 scripts/qdrant-backup-manifest.py \
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
    -F "$config_file" \
    "$node" \
    "${remote_cmd} download ${collection} ${snapshot_name}" \
    < scripts/qdrant-backup-node.sh \
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

python3 scripts/qdrant-s3-retention-plan.py \
  --prefixes-json "${report_dir}/s3-prefixes.json" \
  --keep 2 \
  --protected-backup-id "$backup_id" \
  --output "${report_dir}/delete-prefixes.txt"

while IFS= read -r delete_prefix; do
  [[ -z "$delete_prefix" ]] && continue
  aws s3 rm "s3://${QDRANT_BACKUP_S3_BUCKET}/${delete_prefix}" --recursive
done < "${report_dir}/delete-prefixes.txt"

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  cat "${report_dir}/qdrant-backup-summary.md" >> "$GITHUB_STEP_SUMMARY"
fi
