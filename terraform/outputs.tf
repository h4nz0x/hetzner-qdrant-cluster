output "node_public_ips" {
  description = "Public IPv4 of every node, keyed by name."
  value       = { for name, server in hcloud_server.node : name => server.ipv4_address }
}

output "node_private_ips" {
  description = "Private IPv4 of every node, keyed by name."
  value       = local.node_private_ips
}

output "http_load_balancer_ip" {
  description = "Public IPv4 of the HTTP API load balancer. Point your DNS record here."
  value       = hcloud_load_balancer.http.ipv4
}

output "http_api_url" {
  description = "Base URL clients should use for the REST API."
  value       = local.tls_enabled ? "https://${var.tls_domain}" : "http://${hcloud_load_balancer.http.ipv4}:6333"
}

output "grpc_load_balancer_ip" {
  description = "Public IPv4 of the gRPC load balancer (empty when disabled)."
  value       = var.enable_grpc_load_balancer ? hcloud_load_balancer.grpc[0].ipv4 : ""
}

output "monitoring_load_balancer_ip" {
  description = "Public IPv4 of the optional Grafana load balancer (empty when disabled)."
  value       = var.enable_monitoring_load_balancer ? hcloud_load_balancer.monitoring[0].ipv4 : ""
}

output "ansible_inventory_path" {
  description = "Where Terraform wrote the Ansible inventory."
  value       = local_file.ansible_inventory.filename
}

output "ansible_inventory" {
  description = "Structured inventory data used by scripts/render-inventory.py in CI."
  value       = local.inventory
}
