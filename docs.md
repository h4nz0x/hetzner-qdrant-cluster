# Deploying Qdrant on Hetzner, step by step

This guide takes you from an empty Hetzner account to a running, monitored,
backed-up Qdrant cluster. It assumes you are comfortable in a terminal but have
never used Terraform or Ansible. Every step says **what** you are doing and
**why**, and how to check it worked.

Time needed: about 45 minutes the first time. Cost: about €100/month for the
default 3-node production sizing, or about €40/month with the trial sizing in
step 4.

---

## How the pieces fit together

| Tool | Job | You run it when |
| --- | --- | --- |
| **Terraform** | Creates things *in Hetzner*: servers, volumes, network, firewall, load balancers. | Creating the cluster, resizing it, destroying it. |
| **Ansible** | Configures things *on the servers*: Docker, Qdrant, monitoring, backups. | First deploy, upgrades, config changes. |
| **GitHub Actions** | Runs the same Terraform/Ansible commands from GitHub with approvals and logs. | Optional. Once you want a team workflow instead of your laptop. |

Terraform writes the list of servers it created into a file that Ansible reads
(the *inventory*), so the two never disagree about which machines exist.

---

## Step 1: Install the tools on your computer

You need:

- **terraform** 1.7 or newer: <https://developer.hashicorp.com/terraform/install>
- **ansible** (ansible-core 2.16 or newer): `pipx install ansible-core` or your package manager
- **hcloud** CLI (nice to have): <https://github.com/hetznercloud/cli>
- **git**, **python3**, **ssh**

Check:

```bash
terraform version
ansible --version
python3 --version
```

Clone this repository:

```bash
git clone https://github.com/<you>/hetzner-qdrant-cluster.git
cd hetzner-qdrant-cluster
```

---

## Step 2: Prepare your Hetzner project

1. Log in at <https://console.hetzner.cloud> and create a **project** (for example `qdrant-prod`).
2. In the project, go to **Security → API tokens → Generate API token**, choose **Read & Write**, and copy the token. You will not see it again.
3. Go to **Security → SSH keys → Add SSH key** and paste your public key (`cat ~/.ssh/id_ed25519.pub`). Give it a name you will remember, for example `my-laptop`. Terraform installs this key on every server.

Put the token in your shell (never in a file that is committed):

```bash
export HCLOUD_TOKEN="paste-the-token-here"
```

Optional check with the CLI:

```bash
hcloud context create qdrant-prod     # paste the token when asked
hcloud ssh-key list                    # you should see "my-laptop"
```

---

## Step 3: Find your public IP

The firewall only allows SSH from the addresses you list. Find yours:

```bash
curl -4 ifconfig.me
```

If you work from several places (home, office, VPN), collect all of them. You
can change the list later with a single `terraform apply`.

---

## Step 4: Tell Terraform what you want

```bash
cp terraform/terraform.tfvars.example terraform/terraform.tfvars
```

Open `terraform/terraform.tfvars` in an editor. The two lines you **must**
change:

```hcl
ssh_key_names  = ["my-laptop"]          # the name from step 2
operator_cidrs = ["203.0.113.5/32"]     # your IP from step 3, with /32
```

Things you may want to change:

| Setting | Default | Notes |
| --- | --- | --- |
| `location` / `network_zone` | `nbg1` / `eu-central` | Must match: `nbg1`,`fsn1`,`hel1` → `eu-central`; `ash` → `us-east`; `hil` → `us-west`; `sin` → `ap-southeast`. |
| `server_type` | `ccx23` | Dedicated vCPUs. Use `cx32` for a cheap trial. |
| `node_count` | `3` | Keep 3 unless you know you need more. Qdrant needs a majority of nodes up. |
| `volume_size_gb` | `100` | Your vectors live here. Can be increased later (never decreased). |
| `delete_protection` | `false` | Set to `true` once real data is on the cluster. |
| `tls_domain` | `""` | Leave empty for now; see step 11. |

This file contains no secrets, so it is fine to commit it to your (private)
fork.

---

## Step 5: Create the infrastructure

```bash
make init      # downloads the Hetzner provider (once)
make plan      # shows what will be created; nothing happens yet
```

Read the plan. You should see roughly 25 resources to add: 1 network, 1 subnet,
1 firewall, 3 servers, 3 volumes, 3 attachments, 2 load balancers with their
services and targets, and 1 local file (the inventory). If the plan mentions
destroying anything, stop and re-read your tfvars.

```bash
make apply     # type "yes" when asked
```

This takes 2 to 4 minutes. At the end Terraform prints the outputs:

```
http_load_balancer_ip = "198.51.100.10"
grpc_load_balancer_ip = "198.51.100.11"
node_public_ips = { "qdrant-node-1" = "203.0.113.11", ... }
ansible_inventory_path = ".../ansible/inventories/production/hosts.yml"
```

**Check:** `cat ansible/inventories/production/hosts.yml` lists your three
nodes with their public and private IPs. You can SSH to one:

```bash
ssh root@203.0.113.11     # use your node 1 IP
lsblk                     # you should see a 100G disk mounted at /mnt/HC_Volume_<id>
exit
```

What just happened: Terraform saved a record of everything it created in
`terraform/terraform.tfstate`. **Do not delete that file**; it is how Terraform
knows what it owns. (Step 12 moves it to remote storage.)

---

## Step 6: Create your secrets file

Ansible needs a few secrets. They go in a file that is **never committed**.

```bash
cp ansible/inventories/production/group_vars/all/vault.yml.example \
   ansible/inventories/production/group_vars/all/vault.yml
```

Edit `vault.yml`:

```yaml
vault_qdrant_api_key: "..."             # openssl rand -hex 32
vault_grafana_admin_user: "admin"
vault_grafana_admin_password: "..."     # openssl rand -base64 24
vault_alertmanager_slack_webhook_url: "" # optional, see step 10
vault_qdrant_backup_aws_access_key_id: "..."      # from step 7
vault_qdrant_backup_aws_secret_access_key: "..."  # from step 7
vault_qdrant_backup_slack_webhook_url: ""         # optional
```

You can fill in the backup keys after step 7 and re-run the deploy; nothing
breaks if they are placeholders for now, except the backup playbook.

Then encrypt the file so it is safe on disk:

```bash
ansible-vault encrypt ansible/inventories/production/group_vars/all/vault.yml
```

Choose a vault password and remember it (a password manager is a good place).
Every `make deploy*` command will ask for it. To edit the file later:
`ansible-vault edit <path>`.

---

## Step 7: Create a bucket for backups

Backups are Qdrant snapshots uploaded to S3-compatible storage. Two easy options:

**Option A: Hetzner Object Storage** (same bill, EU data)

1. Console → **Object Storage → Create bucket**, name it (for example
   `my-qdrant-backups`), pick a location (`fsn1`).
2. **Object Storage → Manage credentials → Generate credentials**. Copy the
   access key and secret key into `vault.yml` (step 6).
3. In `ansible/inventories/production/group_vars/all/all.yml` set:

   ```yaml
   qdrant_backup_s3_bucket: my-qdrant-backups
   qdrant_backup_aws_region: fsn1
   qdrant_backup_s3_endpoint_url: "https://fsn1.your-objectstorage.com"
   ```

**Option B: AWS S3**

1. Create a bucket and an IAM user with `s3:PutObject`, `s3:GetObject`,
   `s3:DeleteObject`, `s3:ListBucket` on that bucket only.
2. Put the user's keys in `vault.yml` and set `qdrant_backup_s3_bucket` and
   `qdrant_backup_aws_region` in `all.yml`; leave `qdrant_backup_s3_endpoint_url`
   empty.

Either way, keep the backup bucket in a **different** place from the cluster if
you can. A backup you cannot reach when the cluster is gone is not a backup.

---

## Step 8: Deploy Qdrant, monitoring and backups

```bash
make deps      # installs the Ansible collections (once)
make deploy    # asks for the vault password, then runs everything
```

`make deploy` runs four playbooks in order:

1. **bootstrap**: installs Docker, creates the `qdrant` user, checks the data
   volume is mounted, writes `/etc/qdrant/cluster.env` with the API key, starts
   node_exporter.
2. **qdrant**: starts Qdrant on node 1 (the *bootstrap* node), then on node 2,
   then node 3, each joining the cluster before the next one starts.
3. **monitoring**: starts Prometheus, Alertmanager and Grafana on node 1.
4. **backup-timer**: generates an SSH key on node 1, authorises it on every
   node, installs the backup script and a systemd timer (00:00 and 12:00 UTC).

The first run takes about 5 minutes. Re-running it is safe and fast; Ansible
only changes what differs.

**Check:**

```bash
make status
```

You should see `"peers"` with three entries under `== cluster`, an empty
collection list, and "no backup yet".

---

## Step 9: Talk to your cluster

Get the API URL and key:

```bash
terraform -chdir=terraform output http_api_url        # e.g. http://198.51.100.10:6333
ansible-vault view ansible/inventories/production/group_vars/all/vault.yml | grep api_key
```

Create a collection and insert a point:

```bash
export QDRANT_URL="http://198.51.100.10:6333"
export QDRANT_API_KEY="..."

curl -s -X PUT "$QDRANT_URL/collections/test" \
  -H "api-key: $QDRANT_API_KEY" -H "Content-Type: application/json" \
  -d '{"vectors": {"size": 4, "distance": "Cosine"}, "replication_factor": 2}'

curl -s -X PUT "$QDRANT_URL/collections/test/points?wait=true" \
  -H "api-key: $QDRANT_API_KEY" -H "Content-Type: application/json" \
  -d '{"points": [{"id": 1, "vector": [0.1, 0.2, 0.3, 0.4], "payload": {"hello": "world"}}]}'

curl -s "$QDRANT_URL/collections/test" -H "api-key: $QDRANT_API_KEY" | python3 -m json.tool
```

**Always create collections with `replication_factor: 2` or more.** With 3
nodes and replication 2, any single node can fail without data loss.

The web UI is at `$QDRANT_URL/dashboard` (enter the API key when prompted).
gRPC clients use `<grpc_load_balancer_ip>:6334` with the same key.

---

## Step 10: Look at the dashboards and alerts

Grafana is not exposed to the internet. Open a tunnel:

```bash
make grafana
```

Then browse to <http://localhost:3000> (login from `vault.yml`). The **Qdrant**
dashboard shows requests, latency, collection sizes and Raft state.
Prometheus is at <http://localhost:9090>.

To get alerts in Slack: create an *Incoming Webhook* in Slack, put the URL in
`vault_alertmanager_slack_webhook_url`, then `make deploy-monitoring`. Alerts
fire for a node down, missing Raft peers, disk above 85 %, failed or stale
backups.

If you prefer Grafana on a public URL, set
`enable_monitoring_load_balancer = true` in tfvars and `make apply`. Protect it
with a strong password; it is a login page on the internet.

---

## Step 11: Turn on HTTPS (recommended)

1. Create a DNS `A` record, for example `qdrant.example.com`, pointing at
   `http_load_balancer_ip`.
2. Wait until `dig +short qdrant.example.com` returns that IP.
3. In `terraform.tfvars` set `tls_domain = "qdrant.example.com"` and run
   `make apply`.

Hetzner requests a Let's Encrypt certificate, the load balancer starts
listening on 443 and redirects port 80. Your API URL becomes
`https://qdrant.example.com`. Plain port 6333 on the load balancer is removed.

---

## Step 12: Take a backup and prove it restores

Run one by hand instead of waiting for midnight:

```bash
make backup
```

The summary lists every collection snapshot uploaded to
`s3://<bucket>/production/<timestamp>/`. Then, on the coordinator,
`/var/lib/qdrant-backup/latest/` holds the evidence and Prometheus gets
`qdrant_backup_last_success 1`.

A backup is only useful if it restores. The **restore drill** does that on a
throwaway cluster (nothing touches production):
[docs/runbooks/restore-drill.md](docs/runbooks/restore-drill.md). Run it after
your first real data lands and whenever you change anything backup-related.

---

## Step 13 (optional): Run everything from GitHub Actions

Once the cluster works from your laptop, move the buttons to GitHub so your
team can plan, apply, deploy and back up with an audit trail and approvals.

### 13.1 Remote Terraform state

Terraform state must be shared, so put it in an S3 bucket (a second Hetzner
Object Storage bucket is perfect):

```bash
cp terraform/backend.hcl.example terraform/backend.hcl          # edit bucket/endpoint
cp terraform/backend_override.tf.example terraform/backend_override.tf
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...          # object storage credentials
make init                                                       # answer "yes" to copy local state to the bucket
```

Both files are gitignored; CI recreates them from secrets.

### 13.2 GitHub secrets

In your repository: **Settings → Environments → New environment** named
`production`. Add **required reviewers** (yourself is fine). Then add these
**environment secrets**:

| Secret | Value |
| --- | --- |
| `HCLOUD_TOKEN` | Hetzner API token |
| `TF_STATE_ACCESS_KEY` / `TF_STATE_SECRET_KEY` | credentials for the state bucket |
| `TF_BACKEND_HCL` | the full contents of your `terraform/backend.hcl` |
| `SSH_PRIVATE_KEY` | private key matching one of `ssh_key_names` (`cat ~/.ssh/id_ed25519`) |
| `ANSIBLE_VAULT_YAML` | the **decrypted** contents of `vault.yml` (`ansible-vault view ...`) |
| `BACKUP_S3_ACCESS_KEY` / `BACKUP_S3_SECRET_KEY` / `BACKUP_S3_REGION` / `BACKUP_S3_BUCKET` / `BACKUP_S3_PREFIX` | same values as in `all.yml` and `vault.yml`, used by the restore drill |
| `BACKUP_S3_ENDPOINT_URL` | Hetzner endpoint URL, or leave unset for AWS |

Commit `terraform/terraform.tfvars` to your fork (it has no secrets).

### 13.3 The workflows

| Workflow | Trigger | What it does |
| --- | --- | --- |
| **CI** | every pull request and push to `main` | lint, tests, `terraform validate`, tflint, trivy. On PRs from the same repo it also posts a `terraform plan` comment. |
| **Terraform apply** | manual | choose `plan`, `apply` or `destroy`; type `APPLY`/`DESTROY` to confirm; reviewers approve. |
| **Ansible deploy** | manual | choose a playbook (`site`, `qdrant`, `monitoring`, ...); optional dry run. |
| **Qdrant backup** | manual | runs the systemd backup on the coordinator and shows the summary. |
| **Qdrant restore drill** | manual | restores the latest (or chosen) backup into a disposable cluster on the runner and verifies it. |

Nothing mutating runs on a schedule from GitHub; the 12-hour backup schedule
lives on the coordinator itself, so it keeps working even if GitHub is down.

---

## Everyday tasks

| I want to... | Do this |
| --- | --- |
| add SSH access for a colleague | add their IP to `operator_cidrs`, add their key name to `ssh_key_names`, `make apply` |
| grow the disks | raise `volume_size_gb`, `make apply`, then on each node `resize2fs /dev/disk/by-id/scsi-0HC_Volume_<id>` |
| upgrade Qdrant | see [docs/runbooks/upgrade.md](docs/runbooks/upgrade.md) |
| see what changed before deploying | `make check` |
| get a health report | `make audit` |
| restore production from a backup | see [docs/runbooks/restore-drill.md](docs/runbooks/restore-drill.md), section "Restoring to production" |
| tear everything down | `make destroy` (refuses if `delete_protection = true`) |

## When something goes wrong

See [docs/runbooks/troubleshooting.md](docs/runbooks/troubleshooting.md). The
most common first-run issues are: forgot `export HCLOUD_TOKEN`, IP not in
`operator_cidrs` (SSH times out), and SSH key name not matching Hetzner.
