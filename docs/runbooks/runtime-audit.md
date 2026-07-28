# Qdrant runtime audit

**Trigger:** after the Qdrant live inventory audit is clean, before writing the
Ansible role, after Qdrant container changes, or before a backup/restore drill.

**Safety:** read-only. The workflow SSHes to the three Qdrant nodes and reads
local runtime state. It does not restart containers, run Docker Compose, change
collections, create/delete snapshots, write Terraform state, or run Ansible.

## Run the audit

1. Open **Production Qdrant Runtime Audit** in GitHub Actions.
2. Select branch `main`.
3. Enter exact confirmation:

   ```text
   READ_ONLY_QDRANT_RUNTIME_AUDIT
   ```

4. Approve the protected `production` environment gate.
5. Download artifact `production-qdrant-runtime-audit-<run_id>`.

Required protected secret:

- `QDRANT_SSH_PRIVATE_KEY` - private key that can SSH as `root` to the current
  Qdrant nodes.

The workflow does not require a GitHub Qdrant API-key secret. The streamed
read-only script uses `/etc/qdrant/cluster.env` on each node when that file is
readable.

## What it checks

- Qdrant container is present, running, and on the reviewed image tag.
- Metrics proxy container is present and on the reviewed image tag.
- Local `/readiness` reports `ok`.
- Local `/cluster` reports at least three peers.
- Collections have `replication_factor >= 2`.
- Collections have at least one visible snapshot.
- Qdrant storage usage is captured and below the high-usage threshold.

## Finding severity

| Severity | Meaning | Response |
| --- | --- | --- |
| High | Runtime state threatens availability or recovery. | Reconcile Qdrant before Ansible/import work. |
| Medium | Runtime state differs from the reviewed baseline. | Classify before deployment automation. |

## Follow-up

If this audit is clean, the next safe step is an Ansible role rendered in check
mode. If it reports missing snapshots, prioritize Qdrant backup automation
before any ownership/import work.
