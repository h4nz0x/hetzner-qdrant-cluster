# Day-to-day commands. Run `make help` for the list.
SHELL := /bin/bash
.DEFAULT_GOAL := help

TF_DIR      := terraform
ANSIBLE_DIR := ansible
INVENTORY   := inventories/production
VAULT       := $(ANSIBLE_DIR)/$(INVENTORY)/group_vars/all/vault.yml
VAULT_FLAG  := $(if $(wildcard $(VAULT)),--ask-vault-pass,)
export ANSIBLE_COLLECTIONS_PATH := $(CURDIR)/$(ANSIBLE_DIR)/collections

## ---- Terraform ----------------------------------------------------------
init: ## terraform init (local state unless backend_override.tf exists)
	@test -f $(TF_DIR)/terraform.tfvars || { echo "copy $(TF_DIR)/terraform.tfvars.example to terraform.tfvars first"; exit 1; }
	@if [ -f $(TF_DIR)/backend.hcl ]; then terraform -chdir=$(TF_DIR) init -input=false -backend-config=backend.hcl; \
	 else terraform -chdir=$(TF_DIR) init -input=false; fi

plan: ## show what Terraform would change
	terraform -chdir=$(TF_DIR) plan -input=false

apply: ## create/update Hetzner resources and write the Ansible inventory
	terraform -chdir=$(TF_DIR) apply -input=false

destroy: ## DESTROY everything Terraform created (asks for confirmation)
	terraform -chdir=$(TF_DIR) destroy -input=false

inventory: ## regenerate ansible inventory from Terraform state without applying
	terraform -chdir=$(TF_DIR) output -json > /tmp/qdrant-tf-output.json
	python3 scripts/render-inventory.py --terraform-output /tmp/qdrant-tf-output.json --output $(ANSIBLE_DIR)/$(INVENTORY)/hosts.yml

## ---- Ansible ------------------------------------------------------------
deps: ## install the Ansible collections this repo needs
	cd $(ANSIBLE_DIR) && ansible-galaxy collection install -r requirements.yml -p collections

deploy: ## full deployment: bootstrap + qdrant + monitoring + backup timer
	cd $(ANSIBLE_DIR) && ansible-playbook -i $(INVENTORY) playbooks/site.yml --diff $(VAULT_FLAG)

deploy-qdrant: ## (re)deploy only the Qdrant containers, one node at a time
	cd $(ANSIBLE_DIR) && ansible-playbook -i $(INVENTORY) playbooks/qdrant.yml --diff $(VAULT_FLAG)

deploy-monitoring: ## (re)deploy Prometheus, Alertmanager and Grafana
	cd $(ANSIBLE_DIR) && ansible-playbook -i $(INVENTORY) playbooks/monitoring.yml --diff $(VAULT_FLAG)

deploy-backup: ## (re)install the backup timer on the coordinator
	cd $(ANSIBLE_DIR) && ansible-playbook -i $(INVENTORY) playbooks/backup-timer.yml --diff $(VAULT_FLAG)

check: ## dry run of the full deployment (--check --diff)
	cd $(ANSIBLE_DIR) && ansible-playbook -i $(INVENTORY) playbooks/site.yml --check --diff $(VAULT_FLAG)

audit: ## read-only health report of every node
	mkdir -p /tmp/qdrant-audit
	cd $(ANSIBLE_DIR) && ansible-playbook -i $(INVENTORY) playbooks/audit.yml $(VAULT_FLAG)

## ---- Operations ---------------------------------------------------------
NODE1 = $(shell awk '/ansible_host:/ {print $$2; exit}' $(ANSIBLE_DIR)/$(INVENTORY)/hosts.yml)

status: ## cluster peers, collections and last backup (via SSH to node 1)
	@ssh root@$(NODE1) 'set -a; . /etc/qdrant/cluster.env; set +a; \
	  echo "== cluster"; curl -s -H "api-key: $$QDRANT_API_KEY" http://127.0.0.1:6333/cluster | python3 -m json.tool | head -40; \
	  echo "== collections"; curl -s -H "api-key: $$QDRANT_API_KEY" http://127.0.0.1:6333/collections | python3 -m json.tool; \
	  echo "== last backup"; cat /var/lib/qdrant-backup/latest/qdrant-backup-summary.md 2>/dev/null || echo "no backup yet"; \
	  echo "== timers"; systemctl list-timers qdrant-backup.timer --no-pager'

backup: ## run a snapshot backup now on the coordinator and print the summary
	ssh root@$(NODE1) 'systemctl start --wait qdrant-backup.service && cat /var/lib/qdrant-backup/latest/qdrant-backup-summary.md'

grafana: ## open an SSH tunnel: Grafana on http://localhost:3000, Prometheus on :9090
	@echo "Grafana:    http://localhost:3000"; echo "Prometheus: http://localhost:9090"; echo "Ctrl+C to close"
	ssh -N -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 root@$(NODE1)

## ---- Quality ------------------------------------------------------------
lint: ## run every linter CI runs
	terraform fmt -check -recursive $(TF_DIR)
	terraform -chdir=$(TF_DIR) validate
	yamllint -c .yamllint $(ANSIBLE_DIR) $(TF_DIR) .github
	cd $(ANSIBLE_DIR) && ansible-lint --offline
	shellcheck --severity=warning scripts/*.sh

test: ## run the python test suite
	@for t in tests/*-test.py; do echo "== $$t"; python3 $$t || exit 1; done

help: ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

.PHONY: init plan apply destroy inventory deps deploy deploy-qdrant deploy-monitoring deploy-backup check audit status backup grafana lint test help
