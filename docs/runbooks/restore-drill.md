# Qdrant restore drill

**Trigger:** after a protected Qdrant snapshot backup succeeds, before declaring
Qdrant recoverable, and before any ownership/import work.

**Safety:** restores one selected snapshot from S3 into a disposable local
Qdrant Docker container on the GitHub runner. It does not SSH to live Qdrant,
restore to live Qdrant, change production collections, run Terraform, run
Ansible, or run Docker Compose. The disposable container and local files are
removed at the end of the job.

## Run the drill

1. Open **Production Qdrant Restore Drill** in GitHub Actions.
2. Select branch `main`.
3. Enter exact confirmation:

   ```text
   RUN_QDRANT_RESTORE_DRILL
   ```

4. Enter the backup ID, for example:

   ```text
   2026-07-29T10:27:49Z
   ```

5. Keep `source_node` as `qdrant-node-1` unless testing a different manifest
   node snapshot.
6. Leave `collection` empty to select the first collection in the manifest, or
   set it to a specific collection name.
7. Approve the protected `production` environment gate.
8. Download artifact `production-qdrant-restore-drill-<run_id>`.

Required protected environment secrets:

- `QDRANT_BACKUP_AWS_ACCESS_KEY_ID`
- `QDRANT_BACKUP_AWS_SECRET_ACCESS_KEY`
- `QDRANT_BACKUP_AWS_REGION`
- `QDRANT_BACKUP_S3_BUCKET`
- `QDRANT_BACKUP_S3_PREFIX`

The drill does not require `QDRANT_SSH_PRIVATE_KEY` because it must not connect
to live Qdrant nodes.

## What it does

- Downloads `manifest.json` from:

  ```text
  s3://<bucket>/<prefix>/<backup_id>/manifest.json
  ```

- Selects one manifest snapshot from `source_node` and `collection`.
- Downloads the selected snapshot from S3 to the runner.
- Starts disposable `qdrant/qdrant:v1.17.0`.
- Recovers the snapshot from a local `file://` URI with `priority: snapshot`.
- Verifies:
  - local Qdrant readiness is `ok`;
  - restored collection status is `green` or `yellow`;
  - restored collection has `points_count > 0`;
  - a one-point scroll returns at least one point.

## Evidence

The artifact contains:

- `qdrant-backup-manifest.json` - source backup manifest.
- `qdrant-restore-plan.json` - selected source node, collection, snapshot,
  expected size, and S3 key.
- `qdrant-restore-plan.md` - operator-readable plan summary.
- `qdrant-restore-report.json` - sanitized verification result.
- `qdrant-restore-result.md` - operator-readable verification summary.

## Limitation

This first drill proves the backup object is downloadable and restorable into a
disposable Qdrant process. For a full multi-node disaster recovery exercise,
add a later drill that creates a disposable three-node Qdrant cluster and
restores every node snapshot from the same manifest.
