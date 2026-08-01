# Qdrant snapshot backup

**Trigger:** automatic production backup every 12 hours from a `systemd timer`
on the Qdrant backup coordinator, plus a protected GitHub Actions manual backup
for operator-initiated runs.

**Safety:** creates Qdrant collection snapshots and uploads them to S3. It does
not run Docker Compose, restart containers, change collections, restore data, or
write Terraform state. S3 retention deletes only whole backup prefixes older
than the latest two completed backup sets, and only after the new backup
manifest has uploaded.

## Schedule

The production schedule is installed by Ansible as `qdrant-backup.timer` on
`qdrant-node-1`, the single backup coordinator. It runs at 00:00 and 12:00 UTC:

```text
OnCalendar=*-*-* 00,12:00:00
```

That gives an expected recovery point of up to 12 hours and keeps storage cost
bounded by retaining only the latest two completed backup sets.

Install or update the timer with the protected **Production Qdrant Backup Timer
Rollout** workflow and exact confirmation:

```text
APPLY_QDRANT_BACKUP_TIMER
```

The rollout writes the server-local files:

- `/usr/local/bin/qdrant-snapshot-backup.sh`
- `/usr/local/lib/qdrant-backup/`
- `/etc/qdrant-backup/backup.env`
- `/etc/qdrant-backup/qdrant_ssh_key`
- `/etc/systemd/system/qdrant-backup.service`
- `/etc/systemd/system/qdrant-backup.timer`
- `/etc/systemd/system/qdrant-backup-metrics.service`
- `/etc/systemd/system/qdrant-backup-metrics.timer`
- `/var/lib/node_exporter/textfile/qdrant_backup.prom`

The scheduled path keeps secrets on the coordinator host and does not depend on
GitHub scheduled workflows or repository secrets.

Required protected environment secrets for the rollout:

- `QDRANT_SSH_PRIVATE_KEY`
- `QDRANT_BACKUP_AWS_ACCESS_KEY_ID`
- `QDRANT_BACKUP_AWS_SECRET_ACCESS_KEY`

Optional vault value for backup lifecycle Slack notifications:

- `vault_qdrant_backup_slack_webhook_url`

When set, `qdrant-snapshot-backup.sh` sends best-effort Slack messages to the
Qdrant backup/monitoring channel when a backup starts, succeeds, or fails. A
Slack delivery problem does not fail the backup. The default channel label is
`#qdrant-monitoring-alerts`; the incoming webhook itself remains the routing
source of truth.

Notification colors:

- Started / in progress: bright purple `#a855f7`
- Succeeded: green `#2eb67d`
- Failed: red `#e01e5a`

## Run a manual backup

1. Open **Production Qdrant Snapshot Backup** in GitHub Actions.
2. Select branch `main`.
3. Enter exact confirmation:

   ```text
   CREATE_QDRANT_S3_SNAPSHOT_BACKUP
   ```

4. Approve the protected `production` environment gate.
5. Download artifact `production-qdrant-backup-<run_id>`.

Required protected environment secrets for manual runs:

- `QDRANT_SSH_PRIVATE_KEY`
- `QDRANT_BACKUP_AWS_ACCESS_KEY_ID`
- `QDRANT_BACKUP_AWS_SECRET_ACCESS_KEY`
- `QDRANT_BACKUP_AWS_REGION`
- `QDRANT_BACKUP_S3_BUCKET`
- `QDRANT_BACKUP_S3_PREFIX`

The workflow reads `/etc/qdrant/cluster.env` on each Qdrant node for the local
Qdrant API key. Do not put the Qdrant API key in repository secrets.

GitHub Actions manual backup remains available for operator-triggered backups,
but the automatic production schedule belongs to the systemd timer.

## What it does

- Creates one collection snapshot per collection on each Qdrant node.
- Builds a sanitized manifest for the three-node backup set.
- Streams each snapshot over SSH and uploads it to:

  ```text
  s3://<bucket>/<prefix>/<backup_id>/nodes/<node>/collections/<collection>/<snapshot>
  ```

- Uploads `manifest.json` under the backup-set prefix.
- Keeps only the latest two completed backup-set prefixes in S3.

## Evidence

The artifact contains:

- `qdrant-backup-manifest.json` - node, collection, snapshot, size, checksum,
  and S3 key metadata.
- `qdrant-backup-summary.md` - operator-readable summary.
- `delete-prefixes.txt` - S3 backup prefixes selected for retention deletion.

To verify the deployed retention state without starting a backup or deleting
objects, run the protected **Backup Retention Verification** workflow with:

```text
VERIFY_BACKUP_RETENTION_READ_ONLY
```

That workflow lists `s3://qdrant-backups-example/production/`, downloads
retained `manifest.json` files for validation, verifies at most two backup-set
prefixes and a fresh newest backup, and uploads sanitized evidence as
`backup-retention-verification-<run_id>`. It does not create Qdrant snapshots,
run restore logic, restart containers, or delete S3 objects.

## Monitoring

The rollout also installs `qdrant-backup-metrics.timer` on `qdrant-node-1`.
Every five minutes it reads:

```text
/var/lib/qdrant-backup/latest/qdrant-backup-manifest.json
```

and writes node_exporter textfile metrics to:

```text
/var/lib/node_exporter/textfile/qdrant_backup.prom
```

Central Prometheus scrapes these metrics through the existing `qdrant_node`
target. The key recovery signals are:

- `qdrant_backup_last_success_timestamp_seconds`
- `qdrant_backup_last_success`
- `qdrant_backup_snapshot_objects`
- `qdrant_backup_nodes`
- `qdrant_backup_total_bytes`

On the current live Qdrant hosts, `node_exporter` runs as a standalone Docker
container. The rollout makes that container read the host textfile directory
through:

```text
--collector.textfile.directory=/host/var/lib/node_exporter/textfile
```

This restart affects only the monitoring exporter container, not Qdrant.

## Follow-up

After any backup implementation change, run the disposable restore drill against
the latest backup manifest before declaring the new path production-proven.
