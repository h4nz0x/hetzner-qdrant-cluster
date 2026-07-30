# Qdrant restore drill

**Trigger:** after a protected Qdrant snapshot backup succeeds, before declaring
Qdrant recoverable, and before any ownership/import work.

**Safety:** restores the manifest's node snapshots from S3 into a disposable
three-node Qdrant Docker cluster on the GitHub runner. It does not SSH to live
Qdrant, restore to live Qdrant, change production collections, run Terraform,
run Ansible, or run Docker Compose. The disposable containers, temporary Docker
network, and local files are removed at the end of the job.

## Latest successful drill

Runtime proof: workflow run `30586889983` completed successfully on
2026-07-30. It restored backup `2026-07-30T15:51:46Z` from
`s3://qdrant-backups-example/production/` into a disposable three-node
Qdrant cluster on the self-hosted runner.

Verified evidence:

- Collection: `example_collection`.
- Source manifest nodes: 3.
- Node snapshot uploads: HTTP `200` for `qdrant-node-1`, `qdrant-node-2`, and
  `qdrant-node-3`.
- Total restored snapshot size: `23924484096` bytes.
- Restored collection status: `yellow`.
- Restored points count: `4226643`.
- One-point scroll check returned `1` point.
- Disposable containers, Docker network, and local drill material were removed
  by the cleanup step.

## Find the latest backup to drill

Run the read-only **Production Qdrant Latest Restore Preflight** workflow before
starting a restore drill if you do not already have an exact reviewed backup ID.

1. Open **Production Qdrant Latest Restore Preflight** in GitHub Actions.
2. Select branch `main`.
3. Enter exact confirmation:

   ```text
   FIND_QDRANT_LATEST_RESTORE_BACKUP
   ```

4. Leave `collection` empty to select the first collection present on every
   manifest node, or set a specific collection name.
5. Approve the protected `production` environment gate.
6. Read the workflow summary or download artifact
   `production-qdrant-latest-restore-preflight-<run_id>`.
7. Copy the reported `backup_id` and `collection` into the restore drill
   workflow inputs.

The preflight only lists S3 prefixes and downloads candidate `manifest.json`
files. It does not download snapshot bodies, connect to live Qdrant, SSH to any
server, start Docker containers, run Terraform, run Ansible, restore data, or
delete S3 objects.

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

5. Keep `source_node` as `qdrant-node-1`; it is kept only for workflow input
   compatibility and is ignored by the distributed drill.
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

The latest-backup preflight uses the same S3 secrets and also does not require
`QDRANT_SSH_PRIVATE_KEY`.

## What it does

- Downloads `manifest.json` from:

  ```text
  s3://<bucket>/<prefix>/<backup_id>/manifest.json
  ```

- Selects the requested collection snapshot from every manifest node.
- Downloads all selected node snapshots from S3 to the runner.
- Starts a disposable three-node `qdrant/qdrant:v1.17.0` cluster on a temporary
  Docker network.
- Recovers each node snapshot through that node's
  `/collections/<collection>/snapshots/upload?priority=snapshot` API.
- Verifies:
  - the local Qdrant collection-list API returns `ok`;
  - restored collection status is `green` or `yellow`;
  - restored collection has `points_count > 0`;
  - a one-point scroll returns at least one point.

## Evidence

The artifact contains:

- `qdrant-backup-manifest.json` - source backup manifest.
- `qdrant-restore-plan.json` - selected collection, all node snapshots,
  expected sizes, and S3 keys.
- `qdrant-restore-plan.md` - operator-readable plan summary.
- `qdrant-restore-download.tsv` - sanitized S3 download plan.
- `qdrant-restore-upload.tsv` - sanitized per-node restore upload plan.
- `qdrant-restore-urls.tsv` - disposable node URL mapping on the runner.
- `recover-<node>-response.json` - Qdrant response for each node restore.
- `recover-<node>-status.txt` - HTTP status for each node restore.
- `qdrant-restore-report.json` - sanitized verification result.
- `qdrant-restore-result.md` - operator-readable verification summary.
