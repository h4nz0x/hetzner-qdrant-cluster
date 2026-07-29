#!/usr/bin/env python3
"""Regression tests for Qdrant Ansible check-mode ownership preview."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/production-qdrant-ansible-check.yml"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
PLAYBOOK = ROOT / "ansible/playbooks/qdrant-check-mode.yml"
INVENTORY = ROOT / "ansible/inventories/qdrant-production/hosts.yml"
ROLE_TASKS = ROOT / "ansible/roles/qdrant/tasks/main.yml"
ROLE_DEFAULTS = ROOT / "ansible/roles/qdrant/defaults/main.yml"
COMPOSE_TEMPLATE = ROOT / "ansible/roles/qdrant/templates/docker-compose.yml.j2"
RUNBOOK = ROOT / "docs/runbooks/qdrant-ansible-check-mode.md"


class QdrantAnsibleCheckModeTest(unittest.TestCase):
    def test_inventory_tracks_reviewed_live_nodes_without_secrets(self) -> None:
        inventory = INVENTORY.read_text(encoding="utf-8")
        for expected in (
            "qdrant-node-1",
            "qdrant-node-2",
            "qdrant-node-3",
            "203.0.113.11",
            "203.0.113.12",
            "203.0.113.13",
            "10.2.0.2",
            "10.2.0.3",
            "10.2.0.5",
            "qdrant/qdrant:v1.17.0",
        ):
            self.assertIn(expected, inventory)

        for forbidden in ("QDRANT_API_KEY=", "GRAFANA_ADMIN_PASSWORD", "password:"):
            self.assertNotIn(forbidden, inventory)

    def test_role_is_render_only_and_refuses_normal_apply(self) -> None:
        tasks = ROLE_TASKS.read_text(encoding="utf-8")
        defaults = ROLE_DEFAULTS.read_text(encoding="utf-8")
        self.assertIn("ansible_check_mode", tasks)
        self.assertIn("Run with --check --diff", tasks)
        self.assertIn("ansible.builtin.template", tasks)
        self.assertIn("qdrant_env_file", tasks)
        self.assertIn("/etc/qdrant/cluster.env", defaults)

        for forbidden in (
            "ansible.builtin.systemd",
            "ansible.builtin.service",
            "docker compose up",
            "docker compose stop",
            "docker compose rm",
            "docker_container",
            "state: restarted",
            "state: started",
            "state: absent",
        ):
            self.assertNotIn(forbidden, tasks)

    def test_templates_match_current_qdrant_runtime_shape(self) -> None:
        template = COMPOSE_TEMPLATE.read_text(encoding="utf-8")
        defaults = ROLE_DEFAULTS.read_text(encoding="utf-8")
        for expected in (
            "qdrant_node{{ qdrant_node_id }}",
            "QDRANT__CLUSTER__ENABLED",
            "QDRANT__SERVICE__API_KEY",
            "${QDRANT_API_KEY:?QDRANT_API_KEY is required}",
            "--bootstrap",
            "--uri",
            "metrics_proxy",
        ):
            self.assertIn(expected, template)
        self.assertIn("qdrant/qdrant:v1.17.0", defaults)
        self.assertIn("qdrant_metrics_port: 6336", defaults)

    def test_workflow_is_manual_protected_check_mode_only(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for expected in (
            "workflow_dispatch:",
            "RUN_QDRANT_ANSIBLE_CHECK_MODE",
            "environment: production",
            "QDRANT_SSH_PRIVATE_KEY",
            "StrictHostKeyChecking yes",
            "working-directory: ansible",
            "playbooks/qdrant-check-mode.yml",
            "--check",
            "--diff",
            "python3 ../tests/qdrant-ansible-check-mode-test.py",
        ):
            self.assertIn(expected, workflow)

        for forbidden in (
            "terraform apply",
            "terraform import",
            "docker compose up",
            "docker compose stop",
            "docker compose rm",
            "state=started",
            "state=restarted",
        ):
            self.assertNotIn(forbidden, workflow)

    def test_playbook_runbook_and_ci_are_wired(self) -> None:
        playbook = PLAYBOOK.read_text(encoding="utf-8")
        ci = CI_WORKFLOW.read_text(encoding="utf-8")
        runbook = RUNBOOK.read_text(encoding="utf-8")

        self.assertIn("hosts: qdrant", playbook)
        self.assertIn("- qdrant", playbook)
        self.assertIn("tests/qdrant-ansible-check-mode-test.py", ci)
        self.assertIn("playbooks/qdrant-check-mode.yml", ci)
        self.assertIn("render-only", runbook)
        self.assertIn("RUN_QDRANT_ANSIBLE_CHECK_MODE", runbook)
        self.assertIn("import the live resources", runbook)


if __name__ == "__main__":
    unittest.main()
