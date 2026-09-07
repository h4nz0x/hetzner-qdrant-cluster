terraform {
  required_version = ">= 1.7.0"

  required_providers {
    hcloud = {
      source  = "hetznercloud/hcloud"
      version = "~> 1.50"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

# The token is read from the HCLOUD_TOKEN environment variable.
# Never put it in a .tfvars file.
provider "hcloud" {}
