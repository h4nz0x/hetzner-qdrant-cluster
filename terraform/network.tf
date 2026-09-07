resource "hcloud_network" "this" {
  name              = var.cluster_name
  ip_range          = var.network_ip_range
  delete_protection = var.delete_protection
  labels            = local.common_labels
}

resource "hcloud_network_subnet" "nodes" {
  network_id   = hcloud_network.this.id
  type         = "cloud"
  network_zone = var.network_zone
  ip_range     = var.subnet_ip_range
}
