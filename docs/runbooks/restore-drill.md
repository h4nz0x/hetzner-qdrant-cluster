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

Run the read-only **Production Qdrant Latest Restore Preflight** workflow when
you want to review the latest complete backup before starting a restore drill.

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
7. Use the reported `backup_id` and `collection` as restore drill inputs, or
   use `backup_id=latest` in the restore drill to resolve the same selection
   automatically.

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

4. Enter an exact backup ID, for example:

   ```text
   2026-07-29T10:27:49Z
   ```

   You can also enter:

   ```text
   latest
   ```

   When `backup_id=latest`, the restore drill first runs the same read-only S3
   manifest preflight internally, writes the resolved exact `backup_id` and
   `collection` into the job environment, and then continues with the normal
   disposable restore path.

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

  If the input backup ID is `latest`, the workflow first resolves `latest` to an
  exact timestamped backup ID by listing S3 backup prefixes and validating
  candidate manifests.

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

- `s3-prefixes.json` - S3 backup prefix listing, present when
  `backup_id=latest`.
- `manifest-candidates.tsv` - candidate backup IDs and manifest keys, present
  when `backup_id=latest`.
- `downloaded-manifests.txt` - local candidate manifest files, present when
  `backup_id=latest`.
- `missing-manifests.tsv` - candidate manifest keys that could not be
  downloaded, present when `backup_id=latest`.
- `qdrant-latest-restore-preflight.json` - resolved latest backup selection,
  present when `backup_id=latest`.
- `qdrant-latest-restore-preflight.md` - operator-readable latest backup
  selection summary, present when `backup_id=latest`.
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
