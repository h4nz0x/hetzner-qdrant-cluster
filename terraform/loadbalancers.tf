# ---------------------------------------------------------------------------
# HTTP API load balancer (REST + web UI on 6333)
# ---------------------------------------------------------------------------

resource "hcloud_load_balancer" "http" {
  name               = "${var.cluster_name}-lb"
  load_balancer_type = var.load_balancer_type
  location           = var.location
  delete_protection  = var.delete_protection
  labels             = merge(local.common_labels, { role = "http-lb" })

  algorithm {
    type = "round_robin"
  }
}

resource "hcloud_load_balancer_network" "http" {
  load_balancer_id = hcloud_load_balancer.http.id
  network_id       = hcloud_network.this.id
  ip               = local.lb_http_ip
  depends_on       = [hcloud_network_subnet.nodes]
}

resource "hcloud_load_balancer_target" "http" {
  for_each = hcloud_server.node

  type             = "server"
  load_balancer_id = hcloud_load_balancer.http.id
  server_id        = each.value.id
  use_private_ip   = true
  depends_on       = [hcloud_load_balancer_network.http]
}

resource "hcloud_managed_certificate" "api" {
  count = local.tls_enabled ? 1 : 0

  name         = "${var.cluster_name}-api"
  domain_names = [var.tls_domain]
  labels       = local.common_labels
}

# Plain HTTP on 6333 when no TLS domain is configured. Use this for a trial or
# when TLS is terminated elsewhere (for example Cloudflare in front of it).
resource "hcloud_load_balancer_service" "http" {
  count = local.tls_enabled ? 0 : 1

  load_balancer_id = hcloud_load_balancer.http.id
  protocol         = "http"
  listen_port      = 6333
  destination_port = 6333

  health_check {
    protocol = "http"
    port     = 6333
    interval = 15
    timeout  = 10
    retries  = 3

    http {
      path         = "/healthz"
      status_codes = ["2??"]
    }
  }
}

# HTTPS on 443 with a Hetzner-managed Let's Encrypt certificate.
resource "hcloud_load_balancer_service" "https" {
  count = local.tls_enabled ? 1 : 0

  load_balancer_id = hcloud_load_balancer.http.id
  protocol         = "https"
  listen_port      = 443
  destination_port = 6333

  http {
    certificates  = [hcloud_managed_certificate.api[0].id]
    redirect_http = true
  }

  health_check {
    protocol = "http"
    port     = 6333
    interval = 15
    timeout  = 10
    retries  = 3

    http {
      path         = "/healthz"
      status_codes = ["2??"]
    }
  }
}

# ---------------------------------------------------------------------------
# gRPC load balancer (TCP pass-through on 6334)
# ---------------------------------------------------------------------------

resource "hcloud_load_balancer" "grpc" {
  count = var.enable_grpc_load_balancer ? 1 : 0

  name               = "${var.cluster_name}-grpc-lb"
  load_balancer_type = var.load_balancer_type
  location           = var.location
  delete_protection  = var.delete_protection
  labels             = merge(local.common_labels, { role = "grpc-lb" })

  algorithm {
    type = "round_robin"
  }
}

resource "hcloud_load_balancer_network" "grpc" {
  count = var.enable_grpc_load_balancer ? 1 : 0

  load_balancer_id = hcloud_load_balancer.grpc[0].id
  network_id       = hcloud_network.this.id
  ip               = local.lb_grpc_ip
  depends_on       = [hcloud_network_subnet.nodes]
}

resource "hcloud_load_balancer_target" "grpc" {
  for_each = var.enable_grpc_load_balancer ? hcloud_server.node : {}

  type             = "server"
  load_balancer_id = hcloud_load_balancer.grpc[0].id
  server_id        = each.value.id
  use_private_ip   = true
  depends_on       = [hcloud_load_balancer_network.grpc]
}

resource "hcloud_load_balancer_service" "grpc" {
  count = var.enable_grpc_load_balancer ? 1 : 0

  load_balancer_id = hcloud_load_balancer.grpc[0].id
  protocol         = "tcp"
  listen_port      = 6334
  destination_port = 6334

  health_check {
    protocol = "tcp"
    port     = 6334
    interval = 15
    timeout  = 10
    retries  = 3
  }
}

# ---------------------------------------------------------------------------
# Optional Grafana load balancer (node 1 only)
# ---------------------------------------------------------------------------

resource "hcloud_load_balancer" "monitoring" {
  count = var.enable_monitoring_load_balancer ? 1 : 0

  name               = "${var.cluster_name}-monitoring-lb"
  load_balancer_type = var.load_balancer_type
  location           = var.location
  delete_protection  = var.delete_protection
  labels             = merge(local.common_labels, { role = "monitoring-lb" })

  algorithm {
    type = "round_robin"
  }
}

resource "hcloud_load_balancer_network" "monitoring" {
  count = var.enable_monitoring_load_balancer ? 1 : 0

  load_balancer_id = hcloud_load_balancer.monitoring[0].id
  network_id       = hcloud_network.this.id
  ip               = local.lb_monitoring_ip
  depends_on       = [hcloud_network_subnet.nodes]
}

resource "hcloud_load_balancer_target" "monitoring" {
  count = var.enable_monitoring_load_balancer ? 1 : 0

  type             = "server"
  load_balancer_id = hcloud_load_balancer.monitoring[0].id
  server_id        = hcloud_server.node[local.bootstrap_node].id
  use_private_ip   = true
  depends_on       = [hcloud_load_balancer_network.monitoring]
}

resource "hcloud_load_balancer_service" "monitoring" {
  count = var.enable_monitoring_load_balancer ? 1 : 0

  load_balancer_id = hcloud_load_balancer.monitoring[0].id
  protocol         = "http"
  listen_port      = 3000
  destination_port = 3000

  health_check {
    protocol = "http"
    port     = 3000
    interval = 15
    timeout  = 10
    retries  = 3

    http {
      path         = "/api/health"
      status_codes = ["2??"]
    }
  }
}
