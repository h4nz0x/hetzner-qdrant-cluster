# Troubleshooting

## Terraform

**`Error: hcloud: unauthorized`**
`HCLOUD_TOKEN` is not exported or the token is read-only.

**`ssh key "my-laptop" not found`**
`ssh_key_names` must match the names shown by `hcloud ssh-key list` exactly.

**`operator_cidrs must not open SSH to the whole internet`**
Use your IP with `/32`, not `0.0.0.0/0`.

**`invalid input in field 'network_zone'`**
Location and zone must match; see the table in `terraform.tfvars.example`.

**Managed certificate stays "pending"**
The DNS record must resolve to the load balancer IP *before* setting
`tls_domain`. Remove `tls_domain`, apply, fix DNS, set it again, apply.

## Ansible

**SSH times out**
Your current IP is not in `operator_cidrs`. `curl -4 ifconfig.me`, update the
tfvars, `make apply`.

**`/mnt/HC_Volume_... is not a mount point`**
The volume did not auto-mount (usually right after creation). On the node:
`mount -a`, or reboot it. Check `lsblk` and `/etc/fstab`.

**`vault_qdrant_api_key is undefined`**
`vault.yml` is missing or you ran the playbook without `--ask-vault-pass`.
The `make deploy*` targets add the flag automatically when the file exists.

**Cluster verification fails with fewer peers than expected**
On the missing node: `docker logs qdrant_nodeN`. The usual cause is the
bootstrap node being unreachable on `10.2.0.x:6335`; check the private network
attachment (`ip addr`) and the firewall rule for 6333-6335.

## Qdrant

**`401 Unauthorized`**
Send the key as the `api-key` header (not `Authorization`).

**Collection is `yellow`**
Optimisations or replication in progress. It becomes `green` on its own.
Yellow for hours with a node down means the replicas are degraded; bring the
node back.

**Node shows up twice in `/cluster` after a rebuild**
The old peer id is still registered. Remove it:
`DELETE /cluster/peer/<old_peer_id>?force=true`.

**Load balancer target unhealthy**
The health check hits `GET /healthz` on 6333. On the node:
`curl -s http://127.0.0.1:6333/healthz`. If that works, the firewall must
allow the network CIDR on 6333 (it does by default).

## Backups

**`missing required command: aws`**
The AWS CLI install step did not run (check mode?). `make deploy-backup`.

**`S3 did not return its stored SHA-256 checksum`**
You are using S3-compatible storage without setting
`qdrant_backup_s3_endpoint_url`. Set it so the verifier switches to relaxed
mode, then `make deploy-backup`.

**`could not obtain SSH host key for 10.2.0.x`**
The coordinator cannot reach a node over the private network on port 22.
Hetzner firewalls do not filter private traffic, so check that the node is
attached to the network (`ip addr` shows a `10.2.0.x` address) and that
`sshd` is running (`systemctl status ssh`).

**Backup takes very long**
Large collections stream at network speed between nodes and S3. Increase
`RandomizedDelaySec`, or lower the frequency, but do not exceed your recovery
point objective.

## Getting more information

```bash
make audit                                   # read-only report of every node
ssh root@<node> 'docker ps; docker logs --tail 100 qdrant_node1'
ssh root@<node1> 'journalctl -u qdrant-backup -n 200 --no-pager'
```
