data "hcloud_ssh_key" "operator" {
  for_each = toset(var.ssh_key_names)
  name     = each.key
}

resource "hcloud_server" "node" {
  for_each = toset(local.node_names)

  name         = each.key
  server_type  = var.server_type
  image        = var.image
  location     = var.location
  ssh_keys     = [for key in data.hcloud_ssh_key.operator : key.id]
  firewall_ids = [hcloud_firewall.nodes.id]

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }

  network {
    network_id = hcloud_network.this.id
    ip         = local.node_private_ips[each.key]
  }

  labels = merge(local.common_labels, {
    role    = "qdrant"
    node_id = tostring(index(local.node_names, each.key) + 1)
  })

  delete_protection  = var.delete_protection
  rebuild_protection = var.delete_protection

  # The subnet must exist before a server can join the network.
  depends_on = [hcloud_network_subnet.nodes]

  lifecycle {
    # Changing the image would REPLACE a data node. Upgrade the OS in place
    # with Ansible instead.
    ignore_changes = [image, ssh_keys]
  }
}

# One dedicated volume per node. Hetzner formats it (ext4) and mounts it at
# /mnt/HC_Volume_<id>; Ansible creates <mount>/qdrant on top of that.
resource "hcloud_volume" "data" {
  for_each = toset(local.node_names)

  name              = "${each.key}-data"
  size              = var.volume_size_gb
  location          = var.location
  format            = "ext4"
  delete_protection = var.delete_protection
  labels            = merge(local.common_labels, { node = each.key })
}

resource "hcloud_volume_attachment" "data" {
  for_each = hcloud_volume.data

  volume_id = each.value.id
  server_id = hcloud_server.node[each.key].id
  automount = true
}
