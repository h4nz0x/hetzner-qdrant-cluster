# Restore drill

**Why:** a backup you have never restored is a hope, not a backup. The drill
restores a real backup into a disposable cluster and checks the data. Run it
after the first real data lands, after any change to backup code or the Qdrant
version, and at least monthly.

**Safety:** the drill never connects to production. It reads from S3, runs
Docker on the GitHub runner, and deletes everything at the end.

## Run it

1. GitHub → **Actions → Qdrant restore drill → Run workflow**.
2. `confirmation`: `DRILL`.
3. `backup_id`: `latest`, or an exact ID such as `2026-07-29T10:27:49Z`.
4. `collection`: leave empty to pick the first collection present on every
   node, or name one.
5. `qdrant_image`: keep the default unless production runs another version.
6. Approve the `production` environment.

The job summary shows the plan (which snapshots, sizes) and the result:

- collection status `green` or `yellow`
- `points_count > 0`
- a one-point scroll returns a point

Evidence is attached as the `qdrant-restore-drill-<run_id>` artifact.

Required secrets: `BACKUP_S3_ACCESS_KEY`, `BACKUP_S3_SECRET_KEY`,
`BACKUP_S3_REGION`, `BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIX`, and
`BACKUP_S3_ENDPOINT_URL` for Hetzner Object Storage.

**Limits:** `ubuntu-latest` has about 14 GB of free disk. For larger
collections use a self-hosted runner or run the same steps on a temporary
Hetzner server.

## Run it locally

The workflow is plain bash; you can do the same on any machine with Docker,
the AWS CLI and python3:

```bash
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=fsn1
export AWS_ENDPOINT_URL=https://fsn1.your-objectstorage.com    # Hetzner only
BUCKET=my-qdrant-backups PREFIX=production ID=2026-07-29T10:27:49Z

aws s3 cp "s3://$BUCKET/$PREFIX/$ID/manifest.json" manifest.json
python3 scripts/qdrant-restore-drill.py plan --manifest manifest.json --backup-id "$ID" \
  --snapshot-dir ./snapshots --output-json plan.json --output-md plan.md
# download each s3_key from plan.json, start N qdrant containers on one docker
# network, POST each snapshot to its node's /collections/<c>/snapshots/upload,
# then:
python3 scripts/qdrant-restore-drill.py verify --qdrant-url http://127.0.0.1:<port> \
  --collection <c> --output-json result.json --output-md result.md
```

## Restoring to production

This is a manual, deliberate operation. Do it only when the collection is
lost or corrupted.

1. Stop writers to the affected collection.
2. On each node, download that node's snapshot for the collection from S3
   (the paths are in `manifest.json`).
3. On each node, upload it to the local Qdrant:

   ```bash
   curl -X POST "http://127.0.0.1:6333/collections/<collection>/snapshots/upload?wait=true&priority=snapshot" \
     -H "api-key: $QDRANT_API_KEY" -F "snapshot=@<file>"
   ```

   `priority=snapshot` makes the snapshot win over whatever the cluster
   currently holds for that shard.
4. Check `GET /collections/<collection>` on every node reports `green` and the
   expected `points_count`.
5. Re-enable writers, then run `make backup`.
