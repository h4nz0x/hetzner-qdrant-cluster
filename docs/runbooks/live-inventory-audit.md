# Qdrant live inventory audit

**Trigger:** before importing Qdrant resources, before writing the Ansible role,
after any manual Hetzner console change, or during a quarterly infrastructure
reconciliation.

**Safety:** read-only. The workflow reads Hetzner Cloud metadata and repository
expectations. It does not SSH to hosts, run Docker, call the Qdrant API, write
Terraform state, run Ansible, or change load balancers.

## Run the audit

1. Open **Production Qdrant Live Inventory Audit** in GitHub Actions.
2. Select branch `main`.
3. Enter exact confirmation:

   ```text
   READ_ONLY_QDRANT_LIVE_AUDIT
   ```

4. Approve the protected `production` environment gate.
5. Download artifact `production-qdrant-live-audit-<run_id>`.

The artifact contains:

- `production-qdrant-live-audit.md` - operator-readable summary.
- `production-qdrant-live-audit.json` - sanitized structured evidence.

## What it checks

- Expected Qdrant server names, private IPs, and server types.
- Expected Qdrant volume names, sizes, and server attachments.
- Expected Qdrant load balancer names and private IPs.
- Presence of the Qdrant private network metadata.
- Presence of Qdrant-named firewalls.

## Finding severity

| Severity | Meaning | Response |
| --- | --- | --- |
| High | Recovery-relevant resource missing or mismatched. | Stop import/deploy work and reconcile live state first. |
| Medium | Metadata needs review before ownership transfer. | Classify before writing Terraform or Ansible. |

## Follow-up

If the audit reveals drift, update:

- [`../current-infrastructure-inventory.md`](../current-infrastructure-inventory.md)
- [`../current-server-inventory.csv`](../current-server-inventory.csv)
- [`../qdrant-infrastructure-design.md`](../qdrant-infrastructure-design.md)

The next safe step after a clean audit is an Ansible role in check mode. Do not
restart live Qdrant containers or import Terraform resources from this audit
alone.
