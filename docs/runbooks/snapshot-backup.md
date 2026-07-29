# Qdrant snapshot backup

**Trigger:** automatic production backup every 12 hours from `main`, plus a
protected manual backup for operator-initiated runs.

**Safety:** creates Qdrant collection snapshots and uploads them to S3. It does
not run Docker Compose, restart containers, change collections, restore data,
write Terraform state, or run Ansible. S3 retention deletes only whole backup
prefixes older than the latest two completed backup sets, and only after the
new backup manifest has uploaded.

## Schedule

The workflow runs automatically at:

```text
0 */12 * * *
```

That gives an expected recovery point of up to 12 hours and keeps storage cost
bounded by retaining only the latest two completed backup sets.

Scheduled backups do not use the protected `production` environment because an
environment approval would block unattended execution. These secrets must exist
as repository secrets for the scheduled path:

- `QDRANT_SSH_PRIVATE_KEY`
- `QDRANT_BACKUP_AWS_ACCESS_KEY_ID`
- `QDRANT_BACKUP_AWS_SECRET_ACCESS_KEY`
- `QDRANT_BACKUP_AWS_REGION`
- `QDRANT_BACKUP_S3_BUCKET`
- `QDRANT_BACKUP_S3_PREFIX`

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

## Follow-up

After any backup implementation change, run the disposable restore drill against
the latest backup manifest before declaring the new path production-proven.
