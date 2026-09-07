# Hetzner Cloud firewalls filter the PUBLIC interface only; traffic on the
# private network is never filtered. That is exactly what we want:
#
#   - Qdrant (6333/6334/6335), the metrics proxy (6336), node_exporter (9100)
#     and Grafana (3000) are reached over the private network by the load
#     balancers, the peers and Prometheus, so they need no public rule and are
#     therefore unreachable from the internet.
#   - Only SSH (from operator CIDRs) and ICMP are allowed on the public IP.
#
# Everything not listed here is denied inbound. Outbound is unrestricted
# (apt, Docker Hub, S3 backups).
resource "hcloud_firewall" "nodes" {
  name   = "${var.cluster_name}-nodes"
  labels = local.common_labels

  rule {
    description = "SSH from operator CIDRs"
    direction   = "in"
    protocol    = "tcp"
    port        = "22"
    source_ips  = var.operator_cidrs
  }

  rule {
    description = "ICMP"
    direction   = "in"
    protocol    = "icmp"
    source_ips  = ["0.0.0.0/0", "::/0"]
  }
}
