# Upgrading Qdrant

Qdrant supports rolling upgrades between consecutive minor versions (for
example 1.16 → 1.17). Skipping minors is not supported; upgrade one minor at a
time.

1. Read the release notes for the target version.
2. Take a backup and make sure the last restore drill passed:

   ```bash
   make backup
   ```

3. Change the image in `ansible/inventories/production/group_vars/all/all.yml`:

   ```yaml
   qdrant_image: qdrant/qdrant:v1.18.0
   ```

4. Preview:

   ```bash
   make check
   ```

5. Roll it out. The playbook updates node 1, waits for `/readyz`, then node 2,
   then node 3:

   ```bash
   make deploy-qdrant
   ```

6. Verify:

   ```bash
   make status      # 3 peers, collections green
   make audit       # no findings
   ```

7. Run the restore drill with `qdrant_image` set to the new version.

## Rolling back

Set `qdrant_image` back to the previous tag and `make deploy-qdrant`. Qdrant
storage formats are forward-compatible within a minor; check the release notes
if a version changed the on-disk format.

## Upgrading the OS or Docker

`make deploy` (the `bootstrap` playbook) installs the latest Docker packages
from Docker's repository. For kernel updates, reboot one node at a time and
wait for `make status` to show all peers before the next.

## Resizing

- **Bigger volumes:** raise `volume_size_gb`, `make apply`, then on each node
  `resize2fs /dev/disk/by-id/scsi-0HC_Volume_<id>`. No downtime.
- **Bigger servers:** change `server_type`, `make apply`. Hetzner powers each
  server off to resize it; Terraform does them in parallel, so for zero
  downtime use `terraform apply -target='hcloud_server.node["qdrant-node-2"]'`
  one node at a time.
- **More nodes:** raise `node_count`, `make apply`, `make deploy`. New nodes
  join the cluster but existing shards stay where they are; move shards with
  the cluster API if needed.
