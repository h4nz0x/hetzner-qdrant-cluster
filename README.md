# hetzner-qdrant-cluster

Production-grade, self-hosted [Qdrant](https://qdrant.tech) vector database on
[Hetzner Cloud](https://www.hetzner.com/cloud), built with **Terraform**,
**Ansible** and **GitHub Actions**.

One `terraform apply` and one `ansible-playbook` give you:

- a **3-node Qdrant cluster** (Raft, replication-ready) on dedicated-vCPU servers
- a **private network** with a locked-down Hetzner firewall (only your IP can SSH; Qdrant ports are private)
- a **dedicated data volume** per node (grow it without touching the OS disk)
- an **HTTP/REST load balancer** (optional managed TLS certificate) and a **gRPC load balancer**
- **Prometheus + Alertmanager + Grafana** with a ready-made Qdrant dashboard and alerts
- **verified S3 snapshot backups** every 12 hours (AWS S3 or Hetzner Object Storage) with retention
- a **restore drill** workflow that proves a backup is restorable without touching production
- GitHub Actions for **lint / plan on PR**, and a manual, approval-gated **Deploy** workflow that runs the same `make` targets as your laptop, plus **backup** and **restore drill**

Everything is pinned to exact versions and safe to re-run.

> New to Terraform or Ansible? Start with **[docs.md](docs.md)**, the step-by-step guide.

## Architecture

```
                 Internet
                    │
     ┌──────────────┼───────────────┐
     │  qdrant-lb   │  qdrant-grpc-lb│      Hetzner load balancers
     │  :443/:6333  │  :6334         │      (public IPs, health-checked)
     └──────┬───────┴───────┬────────┘
            │ private network 10.2.0.0/16
   ┌────────┴─────┬─────────┴──────┬──────────────┐
   │ qdrant-node-1│ qdrant-node-2  │ qdrant-node-3│   ccx23 + 100 GB volume each
   │ qdrant :6333 │ qdrant :6333   │ qdrant :6333 │   docker compose, api-key auth
   │ raft   :6335 │ raft   :6335   │ raft   :6335 │   peers talk over private IPs only
   │ metrics:6336 │ metrics:6336   │ metrics:6336 │   nginx proxy, no key needed
   │ prometheus   │                │              │   monitoring + backup coordinator
   │ grafana:3000 │                │              │   live on node 1
   │ backup timer │                │              │
   └──────────────┴────────────────┴──────────────┘
                    │ every 12h: snapshots → verify → S3 → retain latest 2
                    ▼
              S3 bucket (AWS or Hetzner Object Storage)
```

See [docs/architecture.md](docs/architecture.md) for the reasoning behind each choice.

## Repository layout

```
terraform/          Hetzner resources: network, firewall, servers, volumes, load balancers.
                    Also writes the Ansible inventory.
ansible/            Roles: common (Docker, user, volume, secrets), qdrant (compose stack),
                    qdrant_monitoring (Prometheus/Grafana/Alertmanager), qdrant_backup (systemd timer).
scripts/            Backup, verification and restore helpers installed on the coordinator
                    and used by the workflows. Pure bash + python3, no extra dependencies.
tests/              Unit tests for the scripts and repository policy checks (run in CI).
.github/workflows/  ci (lint, test, plan on PRs), deploy (make init/apply/deploy, gated),
                    qdrant-backup, qdrant-restore-drill.
docs/               Architecture notes and operational runbooks.
docs.md             Beginner-friendly deployment guide.
```

## Quick start

```bash
# 0. Prerequisites: terraform >= 1.7, ansible-core >= 2.16, hcloud CLI, an SSH key in Hetzner
export HCLOUD_TOKEN=...            # Hetzner API token (read/write)

# 1. Infrastructure
cp terraform/terraform.tfvars.example terraform/terraform.tfvars   # edit: ssh_key_names, operator_cidrs
make init plan apply               # creates servers, volumes, network, firewall, load balancers
                                   # and writes ansible/inventories/production/hosts.yml

# 2. Secrets
cp ansible/inventories/production/group_vars/all/vault.yml.example \
   ansible/inventories/production/group_vars/all/vault.yml           # edit, then:
ansible-vault encrypt ansible/inventories/production/group_vars/all/vault.yml

# 3. Configuration
make deps deploy                   # Docker, Qdrant cluster, monitoring, backup timer

# 4. Check
make status                        # cluster peers, collections, backup state
```

Full walkthrough with explanations: **[docs.md](docs.md)**.

## Day-2 operations

| Task | How |
| --- | --- |
| Change cluster size, server type, volume size | edit `terraform.tfvars`, `make plan apply`, then `make deploy` (or the Deploy workflow, stage `full`) |
| Upgrade Qdrant | bump `qdrant_image` in `group_vars/all/all.yml`, `make deploy-qdrant` (rolling, one node at a time) |
| Run a backup now | `make backup` or the **Qdrant backup** workflow |
| Prove a backup restores | **Qdrant restore drill** workflow ([runbook](docs/runbooks/restore-drill.md)) |
| Read-only health report | `make audit` |
| Open Grafana | `make grafana` (SSH tunnel to http://localhost:3000) |

Runbooks: [backup](docs/runbooks/backup.md) ·
[restore drill](docs/runbooks/restore-drill.md) ·
[upgrade](docs/runbooks/upgrade.md) ·
[troubleshooting](docs/runbooks/troubleshooting.md)

## Cost (Hetzner list prices, EU, approximate)

| Item | Qty | Monthly |
| --- | --- | --- |
| ccx23 server (4 dedicated vCPU, 16 GB) | 3 | ~€75 |
| 100 GB volume | 3 | ~€15 |
| lb11 load balancer | 2 | ~€12 |
| **Total** | | **~€100** |

Use `server_type = "cx32"` and one load balancer for a ~€40 trial cluster.

## Security model

- The public firewall allows **only SSH from your CIDRs and ICMP**. Qdrant, metrics and Grafana are reachable solely over the private network (load balancers, peers, Prometheus); Hetzner firewalls do not filter private traffic, so no public rule exists for them.
- Every request needs the **API key** (`api-key` header). Prometheus scrapes through an nginx proxy that injects it, so the key never leaves the node.
- SSH is limited to `operator_cidrs`. Password login is disabled.
- Secrets live in an **Ansible Vault** file (gitignored) or in GitHub **environment secrets**; nothing sensitive is committed.
- Mutating workflows run only on manual dispatch, behind a **`production` environment** you can require reviewers on, plus a typed confirmation word.

## Contributing

Issues and pull requests are welcome. `make lint test` runs the same checks as CI.

## License

MIT, see [LICENSE](LICENSE).
