# Qdrant infrastructure design

This document is the design gate before moving the live Qdrant deployment from
manual shell scripts to this infrastructure repository. It is intentionally
non-mutating: do not import, recreate, or redeploy Qdrant resources until the
inventory and restore contract below are reviewed.

## Current state

The live Qdrant estate is tracked as `manual_review` in
[current-infrastructure-inventory.md](current-infrastructure-inventory.md):

| Resource | Current role | Private IP / attachment |
| --- | --- | --- |
| `qdrant-node-1` | Qdrant node | `10.2.0.2`, 512 GiB volume |
| `qdrant-node-2` | Qdrant node | `10.2.0.3`, 512 GiB volume |
| `qdrant-node-3` | Qdrant node | `10.2.0.5`, 512 GiB volume |
| `qdrant-nginx-proxy` | gRPC edge proxy | `10.2.0.6` |
| `qdrant-lb` | HTTP/API load balancer | `10.2.0.4` |
| `qdrant-grpc-lb` | gRPC load balancer | `10.2.0.7` |
| `qdrant-metrics-lb` | metrics load balancer | `10.2.0.8` |

The separate Qdrant repository at `~/Work/example/Qdrant/example-qdrant`
currently deploys with shell scripts:

- `scripts/deploy_multi_host.sh` syncs per-node Docker Compose bundles to
  hard-coded public IPs.
- `scripts/deploy-nginx.sh` syncs the nginx gRPC bundle to a hard-coded public
  IP.
- Secrets are manually placed in `/etc/qdrant/cluster.env`.
- Node storage paths are hard-coded to Hetzner volume mount paths.
- Monitoring runs on `qdrant-node-1` through Prometheus, Grafana, and
  Alertmanager containers.

Graphify was attempted against that repository on 2026-07-28. It detected a
small corpus, but semantic graph extraction could not complete because no
Graphify LLM API key was configured. The design below is based on direct review
of the repository files and the live inventory already captured here.

## Recommendation

Use Ansible for Qdrant deployment and operations.
Do not use Kamal for this layer.

Reason: this is stateful infrastructure, not a stateless application release.
The hard parts are host preparation, mounted volume ownership, firewall policy,
cluster peer configuration, backup scheduling, restore drills, monitoring, and
controlled cutover. Those map cleanly to Terraform plus Ansible. Kamal can
deploy containers, but it does not make the state, restore, and host lifecycle
simple enough for a junior DevOps handoff.

## Target ownership model

Keep Qdrant in this repository, but with its own state boundary:

```text
terraform/
  modules/
    qdrant_cluster/          # future; do not create until import design is reviewed
  environments/
    qdrant-production/       # future; import or recreate live resources here
    qdrant-restore-drill/    # future; disposable restore target

ansible/
  inventories/
    qdrant-production/       # future; generated or reviewed inventory
    qdrant-restore-drill/    # future
  roles/
    qdrant/                  # future
```

State boundary rules:

- Do not put Qdrant resources in the MongoDB `production` Terraform state.
- Keep Qdrant restore drills in a separate backend key from live Qdrant.
- Do not combine Qdrant import, Qdrant deploy, MongoDB changes, and app changes
  in one PR.
- Prefer read-only audit workflows before adding mutating workflows.

## Target topology

Keep the live topology unless a separate capacity review proves it should
change:

- Three Qdrant data nodes in one Hetzner private network.
- One HTTP/API load balancer for port `6333`.
- One gRPC load balancer or nginx edge path for port `6334`.
- Cluster peer traffic on `6335` only between Qdrant nodes.
- Metrics exposed per node, scraped directly by central monitoring rather than
  through client load balancers.

Firewall contract:

| Direction | Ports | Policy |
| --- | --- | --- |
| LB/app subnets -> Qdrant nodes | `6333`, `6334` | Allow only approved app/client paths |
| Qdrant node -> Qdrant node | `6333`, `6334`, `6335` | Private network only |
| Monitoring -> Qdrant nodes | `6336` or direct metrics endpoint | Private monitoring source only |
| Public internet -> Qdrant nodes | any | Deny |
| Public internet -> `6335` | `6335` | Always deny |

## Ansible role shape

The first implementation should model the current compose deployment without
changing behavior:

```text
ansible/roles/qdrant/
  defaults/main.yml
  tasks/main.yml
  templates/docker-compose.yml.j2
  templates/cluster.env.j2
  templates/metrics-proxy.conf.j2
  handlers/main.yml
```

Role responsibilities:

- Install Docker and Docker Compose plugin through the existing common host
  hardening pattern.
- Create the `qdrant` user and `/etc/qdrant`.
- Render `/etc/qdrant/cluster.env` from Ansible Vault, never from committed
  plaintext.
- Render one compose file per host using inventory values for:
  - node name;
  - private peer URI;
  - bootstrap peer;
  - Qdrant image tag;
  - storage mount path;
  - metrics proxy binding.
- Start nodes in deterministic order: bootstrap node first, then peers.
- Verify `/readiness` and `/cluster` after deployment.
- Refuse to run if the storage mount is missing or not writable by `qdrant`.

Use exact image tags. The current shell deployment uses:

- `qdrant/qdrant:v1.17.0`
- `nginx:1.25-alpine` for metrics and gRPC proxy
- `prom/prometheus:v2.35.0`
- `prom/alertmanager:v0.27.0`
- `grafana/grafana:latest`

Replace `grafana/grafana:latest` with a pinned Grafana version before Ansible
owns monitoring.

## Backup and restore contract

Qdrant must not be treated as production-recoverable until there is a successful
restore drill.

Preferred backup model:

- Use Qdrant snapshots per collection.
- Store snapshots in S3-compatible object storage, separate from MongoDB PBM
  prefixes.
- Run scheduled snapshots at least every 12 hours to match the MongoDB recovery
  language unless data owners require a tighter RPO.
- Keep the latest two completed backup sets by default to control object-storage
  cost.
- Never delete the previous completed set until the new set has uploaded and
  passed metadata verification.

Minimum backup evidence per run:

- UTC snapshot time.
- Qdrant version and image tag.
- Collection list.
- Snapshot names, sizes, and checksums where available.
- Upload destination prefix.
- Retention decision.

Restore drill requirements:

- Provision a disposable `qdrant-restore-drill` environment.
- Restore exact named snapshots into the disposable target.
- Verify Qdrant starts cleanly and reports healthy cluster membership.
- Compare collection names, vector counts, payload indexes, and a bounded sample
  query set against pre-restore evidence.
- Destroy drill resources after evidence upload.

## Monitoring

Long term, central monitoring should own Qdrant metrics. Co-locating
Prometheus/Grafana on `qdrant-node-1` is acceptable as the current state, but it
is not the target handoff model.

Required alerts before declaring Qdrant managed:

- Node unavailable.
- Cluster peer missing.
- Collection has insufficient replication for the expected policy.
- Snapshot job failed.
- Newest completed snapshot is older than the configured RPO.
- Disk usage high on attached Qdrant volumes.
- Load balancer health check failing.

## Migration phases

1. Add a read-only Qdrant Hetzner inventory audit workflow.
   - Query live server metadata, volume attachments, load balancers, networks,
     and Qdrant-named firewalls.
   - Upload sanitized evidence.
   - Do not change live servers.
   - Workflow/runbook:
     [qdrant-live-inventory-audit.md](runbooks/qdrant-live-inventory-audit.md).

2. Add a read-only Qdrant runtime audit.
   - Query compose image tags, cluster membership, collection replication, and
     snapshot status through an approved read-only path.
   - Do not restart containers or change collections.

3. Add Ansible inventory and templates in check mode.
   - The first role PR should render the current compose model byte-for-byte
     where possible.
   - Run `ansible-playbook --check --diff` only.

4. Add backup automation.
   - Snapshot all collections.
   - Upload to object storage.
   - Retain only two completed backup sets after successful upload.
   - Add stale-backup alerting.

5. Add disposable restore drill.
   - Restore exact snapshots.
   - Verify collection and query evidence.
   - Tear down automatically.

6. Only after a successful restore drill, decide import vs recreate.
   - Import if live resources match the desired topology and are worth keeping.
   - Recreate only during an approved maintenance/cutover window.

## Cutover boundaries

The first Ansible PR must not:

- restart live Qdrant containers;
- change collection replication;
- change load balancer targets;
- replace nginx edge routing;
- change DNS;
- import Terraform resources;
- delete snapshots or volumes.

The first safe mutating operation is backup automation, because it improves
recoverability without changing client traffic.
