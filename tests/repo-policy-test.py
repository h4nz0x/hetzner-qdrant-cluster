#!/usr/bin/env python3
"""Guardrails that keep this repository safe to publish and fork."""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"hooks\.slack\.com/services/T[0-9A-Z]"),
    re.compile(r"-----BEGIN (RSA|OPENSSH|EC) PRIVATE KEY-----\n[A-Za-z0-9+/]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
)


def tracked_files() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True
    ).stdout
    return [ROOT / name for name in output.decode().split("\0") if name]


class RepoPolicyTest(unittest.TestCase):
    def test_no_secret_looking_strings_in_tracked_files(self) -> None:
        for path in tracked_files():
            if path.suffix in {".png", ".jpg"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for pattern in SECRET_PATTERNS:
                self.assertIsNone(pattern.search(text), f"{path} matches {pattern.pattern}")

    def test_vault_and_state_files_are_never_tracked(self) -> None:
        names = {str(path.relative_to(ROOT)) for path in tracked_files()}
        for forbidden in (
            "ansible/inventories/production/group_vars/all/vault.yml",
            "ansible/inventories/production/hosts.yml",
            "terraform/terraform.tfvars",
            "terraform/backend.hcl",
            "terraform/backend_override.tf",
            ".vault-pass",
        ):
            self.assertNotIn(forbidden, names)
        for path in names:
            self.assertFalse(path.endswith(".tfstate"), path)

    def test_images_are_pinned_to_exact_versions(self) -> None:
        group_vars = (ROOT / "ansible/inventories/production/group_vars/all/all.yml").read_text(encoding="utf-8")
        for key in ("qdrant_image", "prometheus_image", "alertmanager_image", "grafana_image", "node_exporter_image"):
            match = re.search(rf"^{key}:\s*(\S+)$", group_vars, re.MULTILINE)
            self.assertIsNotNone(match, key)
            self.assertNotIn("latest", match.group(1), key)
            self.assertRegex(match.group(1), r":v?\d+\.\d+", key)

    def test_mutating_workflows_are_manual_and_gated(self) -> None:
        for name in ("terraform-apply", "ansible-deploy", "qdrant-backup", "qdrant-restore-drill"):
            workflow = (ROOT / f".github/workflows/{name}.yml").read_text(encoding="utf-8")
            self.assertIn("workflow_dispatch:", workflow, name)
            self.assertNotIn("schedule:", workflow, name)
            self.assertIn("environment: production", workflow, name)
            self.assertIn("runs-on: ubuntu-latest", workflow, name)

    def test_terraform_firewall_never_opens_ssh_to_the_world_by_default(self) -> None:
        variables = (ROOT / "terraform/variables.tf").read_text(encoding="utf-8")
        self.assertIn('!contains(var.operator_cidrs, "0.0.0.0/0")', variables)
        firewall = (ROOT / "terraform/firewall.tf").read_text(encoding="utf-8")
        self.assertIn("source_ips  = var.operator_cidrs", firewall)
        for port in ("6333", "6334", "6335", "3000", "9100"):
            self.assertNotIn(f'port        = "{port}"', firewall)


if __name__ == "__main__":
    unittest.main()
