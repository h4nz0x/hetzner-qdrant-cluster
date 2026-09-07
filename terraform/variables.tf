# ---------------------------------------------------------------------------
# Cluster identity
# ---------------------------------------------------------------------------

variable "cluster_name" {
  description = "Name prefix for every Hetzner resource (servers, volumes, network, firewalls, load balancers)."
  type        = string
  default     = "qdrant"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.cluster_name))
    error_message = "cluster_name must be lowercase letters, digits and dashes, 2-31 characters."
  }
}

variable "environment" {
  description = "Environment label attached to every resource, for example production or staging."
  type        = string
  default     = "production"
}

variable "labels" {
  description = "Extra Hetzner labels merged into every resource (owner, cost_center, ...)."
  type        = map(string)
  default     = {}
}

# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

variable "location" {
  description = "Hetzner Cloud location: nbg1, fsn1, hel1 (EU), ash, hil (US), sin (Asia)."
  type        = string
  default     = "nbg1"
}

variable "network_zone" {
  description = "Network zone matching the location: eu-central (nbg1/fsn1/hel1), us-east (ash), us-west (hil), ap-southeast (sin)."
  type        = string
  default     = "eu-central"
}

# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

variable "node_count" {
  description = "Number of Qdrant data nodes. Three is the minimum for a production cluster (Raft consensus needs a majority)."
  type        = number
  default     = 3

  validation {
    condition     = var.node_count >= 3 && var.node_count <= 9
    error_message = "node_count must be between 3 and 9."
  }
}

variable "server_type" {
  description = "Hetzner server type for each node. ccx23 (4 dedicated vCPU, 16 GB) is a sensible production baseline; cx32 is cheaper for a trial."
  type        = string
  default     = "ccx23"
}

variable "image" {
  description = "Operating system image. The Ansible roles target Ubuntu 24.04 LTS."
  type        = string
  default     = "ubuntu-24.04"
}

variable "volume_size_gb" {
  description = "Size in GB of the dedicated data volume attached to each node. Qdrant storage lives on this volume, not on the root disk."
  type        = number
  default     = 100

  validation {
    condition     = var.volume_size_gb >= 10 && var.volume_size_gb <= 10240
    error_message = "volume_size_gb must be between 10 and 10240."
  }
}

variable "ssh_key_names" {
  description = "Names of SSH keys that ALREADY exist in your Hetzner project (hcloud ssh-key list). They are installed on every node for root."
  type        = list(string)

  validation {
    condition     = length(var.ssh_key_names) > 0
    error_message = "At least one existing Hetzner SSH key name is required."
  }
}

variable "delete_protection" {
  description = "Enable Hetzner delete/rebuild protection on servers and volumes. Set true once the cluster carries real data."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Network and access
# ---------------------------------------------------------------------------

variable "network_ip_range" {
  description = "Private network CIDR for the cluster."
  type        = string
  default     = "10.2.0.0/16"
}

variable "subnet_ip_range" {
  description = "Subnet CIDR inside network_ip_range that holds the nodes and load balancers."
  type        = string
  default     = "10.2.0.0/24"
}

variable "operator_cidrs" {
  description = "CIDRs allowed to SSH (port 22) to the nodes, for example your office or VPN egress IP as 203.0.113.5/32."
  type        = list(string)

  validation {
    condition = (
      length(var.operator_cidrs) > 0 &&
      !contains(var.operator_cidrs, "0.0.0.0/0") &&
      !contains(var.operator_cidrs, "::/0")
    )
    error_message = "operator_cidrs must be non-empty and must not open SSH to the whole internet."
  }
}

# ---------------------------------------------------------------------------
# Load balancers
# ---------------------------------------------------------------------------

variable "load_balancer_type" {
  description = "Hetzner load balancer type. lb11 handles up to 25 targets and is fine for most clusters."
  type        = string
  default     = "lb11"
}

variable "tls_domain" {
  description = <<-EOT
    Optional DNS name for the HTTP API load balancer, e.g. qdrant.example.com.
    When set, Hetzner issues a managed Let's Encrypt certificate and the load
    balancer serves HTTPS on 443. The DNS record must already point at the load
    balancer IP before you set this (apply once without it, create the record,
    then apply again with it).
  EOT
  type        = string
  default     = ""
}

variable "enable_grpc_load_balancer" {
  description = "Create a second TCP load balancer for the gRPC API on port 6334."
  type        = bool
  default     = true
}

variable "enable_monitoring_load_balancer" {
  description = "Expose Grafana (node 1, port 3000) through a dedicated load balancer. Off by default: reach Grafana over an SSH tunnel instead."
  type        = bool
  default     = false
}
