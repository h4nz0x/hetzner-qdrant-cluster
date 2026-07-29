# Qdrant Ansible check mode

**Trigger:** after clean Qdrant inventory/runtime audits and a successful
Qdrant restore drill, before importing or recreating Qdrant infrastructure.

**Safety:** render-only. The workflow runs Ansible with `--check --diff` against
the live Qdrant hosts. The role fails closed if check mode is not enabled. It
does not restart containers, run Docker Compose, change Qdrant collections,
write Terraform state, import resources, or edit secrets.

## Run the check

1. Open **Production Qdrant Ansible Check Mode** in GitHub Actions.
2. Select branch `main`.
3. Enter exact confirmation:

   ```text
   RUN_QDRANT_ANSIBLE_CHECK_MODE
   ```

4. Approve the protected `production` environment gate.
5. Review the job summary and artifact
   `production-qdrant-ansible-check-<run_id>`.

Required protected environment secret:

- `QDRANT_SSH_PRIVATE_KEY`

## What it checks

- The separate `qdrant-production` Ansible inventory still describes the three
  reviewed live nodes.
- `/etc/qdrant/cluster.env` exists on each node. The role does not read or
  publish secret values.
- The rendered `docker-compose.yml` and metrics proxy template match the
  desired current model.

## Follow-up

If the diff is empty or expected, the next safe step is a reviewed decision on
Qdrant Terraform ownership: import the live resources if they match the desired
topology, or recreate only during an approved maintenance/cutover window.
