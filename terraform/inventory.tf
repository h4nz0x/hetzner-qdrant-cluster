# Terraform writes the Ansible inventory so the two tools never disagree about
# which servers exist. The same data is exposed as the `ansible_inventory`
# output so CI can regenerate the file with scripts/render-inventory.py.
locals {
  inventory_nodes = [
    for i, name in local.node_names : {
      name       = name
      node_id    = i + 1
      public_ip  = hcloud_server.node[name].ipv4_address
      private_ip = local.node_private_ips[name]
      data_dir   = "/mnt/HC_Volume_${hcloud_volume.data[name].id}/qdrant"
    }
  ]

  inventory = {
    cluster_name         = var.cluster_name
    environment          = var.environment
    bootstrap_private_ip = local.node_private_ips[local.bootstrap_node]
    nodes                = local.inventory_nodes
    load_balancers = {
      http_public_ip       = hcloud_load_balancer.http.ipv4
      http_private_ip      = local.lb_http_ip
      grpc_public_ip       = var.enable_grpc_load_balancer ? hcloud_load_balancer.grpc[0].ipv4 : ""
      monitoring_public_ip = var.enable_monitoring_load_balancer ? hcloud_load_balancer.monitoring[0].ipv4 : ""
      network_ip_range     = var.network_ip_range
    }
  }
}

resource "local_file" "ansible_inventory" {
  filename        = "${path.module}/../ansible/inventories/production/hosts.yml"
  file_permission = "0644"
  content         = templatefile("${path.module}/templates/hosts.yml.tpl", local.inventory)
}
