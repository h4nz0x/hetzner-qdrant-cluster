locals {
  common_labels = merge(
    {
      managed_by  = "terraform"
      cluster     = var.cluster_name
      environment = var.environment
      service     = "qdrant"
    },
    var.labels,
  )

  node_names = [for i in range(var.node_count) : "${var.cluster_name}-node-${i + 1}"]

  # Nodes occupy .11 upward; load balancers take .2, .3 and .4 of the subnet.
  node_private_ips = { for i, name in local.node_names : name => cidrhost(var.subnet_ip_range, 11 + i) }
  lb_http_ip       = cidrhost(var.subnet_ip_range, 2)
  lb_grpc_ip       = cidrhost(var.subnet_ip_range, 3)
  lb_monitoring_ip = cidrhost(var.subnet_ip_range, 4)

  bootstrap_node = local.node_names[0]
  tls_enabled    = var.tls_domain != ""
}
