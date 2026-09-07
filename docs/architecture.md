# Architecture and design decisions

## Topology

| Component | Count | Purpose |
| --- | --- | --- |
| Qdrant node (`ccx23`, 100 GB volume) | 3 | Data and Raft consensus. Three is the minimum for a majority. |
| Private network `10.2.0.0/16` | 1 | All node-to-node and LB-to-node traffic. Nodes never accept Qdrant traffic on their public IPs. |
| Firewall | 1 | SSH from operator CIDRs; Qdrant/metrics ports from the private network only. |
| HTTP load balancer | 1 | REST API and web UI. HTTPS with a managed certificate when `tls_domain` is set. |
| gRPC load balancer | 1 (optional) | TCP pass-through for gRPC clients on 6334. |
| Monitoring stack | on node 1 | Prometheus, Alertmanager, Grafana. Co-located to keep cost down; move it out if you add a central monitoring host. |
| Backup coordinator | on node 1 | systemd timer that snapshots every collection on every node and uploads to S3. |

## Why Terraform + Ansible, not Kubernetes or a container platform

Qdrant is stateful infrastructure. The hard parts are volumes, firewalling,
peer configuration, backups, restore drills and controlled upgrades, and those
map directly to Terraform (cloud resources) and Ansible (host configuration).
Docker Compose on each node is enough to run Qdrant; a scheduler adds moving
parts without solving any of the hard parts.

## Why the private network carries everything

Hetzner load balancers reach their targets over the private network
(`use_private_ip = true`), and Hetzner Cloud firewalls filter only the public
interface. So Qdrant, the metrics proxy, node_exporter and Grafana listen on
all interfaces but are reachable exclusively through the private network: the
firewall exposes nothing but SSH (operator CIDRs) and ICMP publicly. Port 6335
(Raft) is never reachable from the internet.

## Why an nginx metrics proxy

Qdrant's `/metrics` endpoint requires the API key. Rather than give Prometheus
the key, each node runs a tiny nginx (`:6336`) that injects the `api-key`
header for `/metrics` only. Prometheus scrapes 6336 over the private network.

## Why backups are Qdrant snapshots streamed to S3

- Qdrant snapshots are consistent per collection and per node; restoring the
  set from every node rebuilds the collection with its shards.
- The coordinator streams each snapshot over SSH, recomputing size and SHA-256
  while streaming, uploads it, reads the object back with `head-object`, and
  only then deletes the local snapshot through the Qdrant API. A local
  snapshot is never removed before S3 has confirmed the bytes.
- Retention keeps the newest N *complete* backup sets (a set is complete only
  when its `manifest.json` uploaded). A failed run cannot evict a good set.
- With AWS S3 the verifier additionally requires the S3-computed checksum and
  server-side encryption. S3-compatible stores (Hetzner Object Storage) do
  not return those, so `qdrant_backup_s3_endpoint_url` switches to relaxed
  verification automatically.

## Why the coordinator has its own SSH key

The backup job on node 1 must fetch snapshots from nodes 2 and 3. The
`backup-timer` playbook generates an ed25519 key on node 1 and authorises its
public half on every node. No private key is stored in Ansible Vault or GitHub.

## Why the restore drill runs on the GitHub runner

Restoring into production to test a backup is how outages start. The drill
downloads the snapshots, starts a throwaway Qdrant cluster in Docker on the
runner, uploads each node's snapshot, and checks the collection is green with
points. It needs only S3 credentials, never SSH to production.

Runner disk is limited (about 14 GB free on `ubuntu-latest`). For collections
larger than that, run the drill on a self-hosted runner or a temporary Hetzner
server.

## Upgrade path

Qdrant supports rolling upgrades between adjacent minor versions. The `qdrant`
playbook deploys the bootstrap node first and the remaining nodes one at a
time (`serial: 1`), waiting for `/readyz` on each. See
[runbooks/upgrade.md](runbooks/upgrade.md).

## What is deliberately not included

- **Multi-region**: Hetzner private networks are per-network-zone.
- **Autoscaling**: change `node_count` and apply. Qdrant does not rebalance
  shards automatically; new nodes receive shards for new collections or after a
  manual shard move.
- **Client TLS to nodes**: TLS terminates at the load balancer. Node-to-node
  traffic is on the private network.
