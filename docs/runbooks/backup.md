# Backups

**What:** every 12 hours the coordinator (node 1) snapshots every collection on
every node, verifies each snapshot while streaming it to S3, verifies the
uploaded object, uploads a manifest, prunes old backup sets, then deletes the
local snapshots through the Qdrant API.

**Schedule:** `qdrant-backup.timer` on the coordinator, `*-*-* 00,12:00:00`
UTC with up to 15 minutes random delay. Change with
`qdrant_backup_on_calendar` in `group_vars/all/all.yml` and
`make deploy-backup`.

**Retention:** the newest `qdrant_backup_retention_keep` (default 2) complete
backup sets. A set is complete only when `manifest.json` exists under its
prefix.

**Layout in S3:**

```
s3://<bucket>/<prefix>/<backup_id>/manifest.json
s3://<bucket>/<prefix>/<backup_id>/nodes/<node>/collections/<collection>/<snapshot>
```

`backup_id` is the UTC start time, for example `2026-07-29T10:27:49Z`.

## Run a backup now

```bash
make backup
```

or the **Qdrant backup** GitHub workflow (type `BACKUP`).

## Check the last backup

On the coordinator:

```bash
systemctl list-timers qdrant-backup.timer
journalctl -u qdrant-backup.service -n 100 --no-pager
cat /var/lib/qdrant-backup/latest/qdrant-backup-summary.md
cat /var/lib/qdrant-backup/latest/qdrant-local-snapshot-cleanup.json
```

In Prometheus / Grafana:

| Metric | Healthy value |
| --- | --- |
| `qdrant_backup_last_success` | `1` |
| `qdrant_backup_last_success_timestamp_seconds` | less than 13 hours old |
| `qdrant_backup_local_snapshots_remaining` | `0` |
| `qdrant_backup_snapshot_objects` | nodes × collections |

Alerts `QdrantBackupFailed` and `QdrantBackupStale` fire otherwise.

## Failure behaviour

- Any failure before S3 verification leaves the local snapshots in place for
  investigation and exits non-zero. Slack gets a red message if a webhook is
  configured.
- An incomplete prefix (no `manifest.json`) is ignored by retention and never
  counts as a backup. Delete it by hand after confirming the run failed.
- Local cleanup happens only after every object and the manifest verified.

## Change the bucket or credentials

Edit `all.yml` (bucket, prefix, region, endpoint) and `vault.yml`
(credentials), then `make deploy-backup`. Run `make backup` to confirm.
