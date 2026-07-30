#!/usr/bin/env python3
"""Regression tests for Ansible-managed Qdrant backup scheduling."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/production-qdrant-backup-timer-rollout.yml"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
PLAYBOOK = ROOT / "ansible/playbooks/qdrant-backup-timer.yml"
INVENTORY = ROOT / "ansible/inventories/qdrant-production/hosts.yml"
GROUP_VARS = ROOT / "ansible/inventories/qdrant-production/group_vars/all/all.yml"
VAULT_EXAMPLE = ROOT / "ansible/inventories/qdrant-production/group_vars/all/vault.yml.example"
TASKS = ROOT / "ansible/roles/qdrant_backup/tasks/main.yml"
DEFAULTS = ROOT / "ansible/roles/qdrant_backup/defaults/main.yml"
SERVICE = ROOT / "ansible/roles/qdrant_backup/templates/qdrant-backup.service.j2"
TIMER = ROOT / "ansible/roles/qdrant_backup/templates/qdrant-backup.timer.j2"
ENV_TEMPLATE = ROOT / "ansible/roles/qdrant_backup/templates/backup.env.j2"
METRICS_SERVICE = ROOT / "ansible/roles/qdrant_backup/templates/qdrant-backup-metrics.service.j2"
METRICS_TIMER = ROOT / "ansible/roles/qdrant_backup/templates/qdrant-backup-metrics.timer.j2"
SCRIPT = ROOT / "scripts/qdrant-systemd-snapshot-backup.sh"
METRICS_SCRIPT = ROOT / "scripts/qdrant-backup-metrics.py"
RUNBOOK = ROOT / "docs/runbooks/qdrant-snapshot-backup.md"


class QdrantBackupTimerTest(unittest.TestCase):
    def test_inventory_has_single_coordinator_and_no_secret_values(self) -> None:
        inventory = INVENTORY.read_text(encoding="utf-8")
        vault_example = VAULT_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn("qdrant_backup_coordinator:", inventory)
        self.assertIn("qdrant-node-1: {}", inventory)
        self.assertIn("vault_qdrant_backup_aws_access_key_id", vault_example)
        self.assertIn("vault_qdrant_backup_aws_secret_access_key", vault_example)
        self.assertIn("vault_qdrant_backup_ssh_private_key", vault_example)
        for forbidden in ("AKIA", "QDRANT_API_KEY=", "BEGIN OPENSSH PRIVATE KEY-----\n  b"):
            self.assertNotIn(forbidden, inventory + vault_example)

    def test_role_installs_systemd_timer_with_12_hour_schedule_and_two_retention(self) -> None:
        tasks = TASKS.read_text(encoding="utf-8")
        defaults = DEFAULTS.read_text(encoding="utf-8")
        service = SERVICE.read_text(encoding="utf-8")
        timer = TIMER.read_text(encoding="utf-8")
        env_template = ENV_TEMPLATE.read_text(encoding="utf-8")
        metrics_service = METRICS_SERVICE.read_text(encoding="utf-8")
        metrics_timer = METRICS_TIMER.read_text(encoding="utf-8")
        group_vars = GROUP_VARS.read_text(encoding="utf-8")

        for required in (
            "APPLY_QDRANT_BACKUP_TIMER",
            "qdrant-backup.service",
            "qdrant-backup.timer",
            "qdrant-systemd-snapshot-backup.sh",
            "qdrant-backup-node.sh",
            "qdrant-backup-manifest.py",
            "qdrant-backup-metrics.py",
            "qdrant-backup-metrics.service",
            "qdrant-backup-metrics.timer",
            "Inspect standalone Docker node_exporter",
            "Recreate standalone Docker node_exporter",
            "--collector.textfile.directory=/host",
            "qdrant-s3-retention-plan.py",
            "Install Qdrant backup AWS CLI",
            "qdrant_backup_awscli_url",
            "/usr/local/bin/aws --version",
            "Preview Qdrant backup timer enablement",
            "when: ansible_check_mode",
            "when: not ansible_check_mode",
            "enabled:",
            "state:",
        ):
            self.assertIn(required, tasks)
        self.assertIn('qdrant_backup_on_calendar: "*-*-* 00,12:00:00"', defaults)
        self.assertIn("qdrant_backup_retention_keep: 2", defaults)
        self.assertIn('qdrant_backup_on_calendar: "*-*-* 00,12:00:00"', group_vars)
        self.assertIn("qdrant_backup_retention_keep: 2", group_vars)
        self.assertIn("ExecStart={{ qdrant_backup_script_path }}", service)
        self.assertIn("OnCalendar={{ qdrant_backup_on_calendar }}", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("AWS_ACCESS_KEY_ID={{ vault_qdrant_backup_aws_access_key_id | quote }}", env_template)
        self.assertIn("qdrant-backup-metrics.py", metrics_service)
        self.assertIn("{{ qdrant_backup_state_dir }}/latest/qdrant-backup-manifest.json", metrics_service)
        self.assertIn("qdrant_backup_metrics_textfile_dir: /var/lib/node_exporter/textfile", defaults)
        self.assertIn("qdrant_backup_metrics_file:", defaults)
        self.assertIn("OnUnitActiveSec=5m", metrics_timer)

    def test_systemd_backup_script_uploads_manifest_and_prunes_to_two(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        for required in (
            "/etc/qdrant-backup/backup.env",
            "/var/lib/qdrant-backup",
            "/usr/local/lib/qdrant-backup",
            "qdrant-node-1",
            "qdrant-node-2",
            "qdrant-node-3",
            "qdrant-backup-manifest.py",
            "qdrant-s3-retention-plan.py",
            "QDRANT_BACKUP_RETENTION_KEEP:-2",
            "aws s3 cp",
            "aws s3 rm",
        ):
            self.assertIn(required, script)
        for forbidden in ("docker compose", "terraform", "systemctl restart qdrant"):
            self.assertNotIn(forbidden, script)

    def test_rollout_workflow_is_manual_protected_and_runs_preview_then_apply(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "workflow_dispatch:",
            "APPLY_QDRANT_BACKUP_TIMER",
            "environment: production",
            "QDRANT_BACKUP_AWS_ACCESS_KEY_ID",
            "QDRANT_BACKUP_AWS_SECRET_ACCESS_KEY",
            "QDRANT_SSH_PRIVATE_KEY",
            "playbooks/qdrant-backup-timer.yml",
            "--check",
            "--diff",
            "qdrant_backup_timer_confirmation=APPLY_QDRANT_BACKUP_TIMER",
            "python3 tests/qdrant-backup-timer-test.py",
        ):
            self.assertIn(required, workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("terraform apply", workflow)
        self.assertNotIn("docker compose up", workflow)

    def test_ci_and_runbook_are_updated_for_systemd_schedule(self) -> None:
        ci = CI_WORKFLOW.read_text(encoding="utf-8")
        playbook = PLAYBOOK.read_text(encoding="utf-8")
        runbook = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("python tests/qdrant-backup-timer-test.py", ci)
        self.assertIn("playbooks/qdrant-backup-timer.yml", ci)
        self.assertIn("hosts: qdrant_backup_coordinator", playbook)
        self.assertIn("- qdrant_backup", playbook)
        self.assertIn("systemd timer", runbook)
        self.assertIn("00:00 and 12:00 UTC", runbook)
        self.assertIn("GitHub Actions manual backup", runbook)

    def test_qdrant_backup_metrics_script_exports_manifest_health(self) -> None:
        script = METRICS_SCRIPT.read_text(encoding="utf-8")
        for required in (
            "qdrant_backup_last_success_timestamp_seconds",
            "qdrant_backup_last_success",
            "qdrant_backup_snapshot_objects",
            "qdrant_backup_nodes",
            "qdrant_backup_total_bytes",
            "qdrant_backup_manifest_present",
            "qdrant_backup_metric_export_timestamp_seconds",
        ):
            self.assertIn(required, script)
        self.assertIn("qdrant_backup.prom", script)


if __name__ == "__main__":
    unittest.main()
