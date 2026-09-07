#!/usr/bin/env python3
"""Tests for scripts/render-inventory.py and the Terraform inventory template."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render-inventory.py"
EXAMPLE = ROOT / "ansible" / "inventories" / "hosts.yml.example"
TEMPLATE = ROOT / "terraform" / "templates" / "hosts.yml.tpl"

spec = importlib.util.spec_from_file_location("render_inventory", SCRIPT)
assert spec and spec.loader
RENDER = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = RENDER
spec.loader.exec_module(RENDER)


def terraform_output(node_count: int = 3) -> dict:
    nodes = [
        {
            "name": f"qdrant-node-{i}",
            "node_id": i,
            "public_ip": f"203.0.113.{10 + i}",
            "private_ip": f"10.2.0.{10 + i}",
            "data_dir": f"/mnt/HC_Volume_10000000{i}/qdrant",
        }
        for i in range(1, node_count + 1)
    ]
    return {
        "ansible_inventory": {
            "value": {
                "cluster_name": "qdrant",
                "environment": "production",
                "bootstrap_private_ip": "10.2.0.11",
                "nodes": nodes,
                "load_balancers": {
                    "http_public_ip": "198.51.100.10",
                    "http_private_ip": "10.2.0.2",
                    "grpc_public_ip": "198.51.100.11",
                    "monitoring_public_ip": "",
                    "network_ip_range": "10.2.0.0/16",
                },
            }
        }
    }


class RenderInventoryTest(unittest.TestCase):
    def test_rendered_inventory_matches_committed_example(self) -> None:
        rendered = RENDER.render(RENDER.load_inventory(terraform_output()))
        expected = EXAMPLE.read_text(encoding="utf-8")
        strip = lambda text: [l for l in text.splitlines() if not l.startswith("#")]  # noqa: E731
        self.assertEqual(strip(rendered), strip(expected))

    def test_every_node_gets_host_vars_and_first_node_is_coordinator(self) -> None:
        rendered = RENDER.render(RENDER.load_inventory(terraform_output(5)))
        for i in range(1, 6):
            self.assertIn(f"        qdrant-node-{i}:\n          ansible_host: 203.0.113.{10 + i}", rendered)
        self.assertEqual(rendered.count("        qdrant-node-1: {}"), 3)
        self.assertIn("qdrant_node_count: 5", rendered)

    def test_cli_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "out.json"
            dst = Path(tmp) / "hosts.yml"
            src.write_text(json.dumps(terraform_output()), encoding="utf-8")
            self.assertEqual(RENDER.main(["--terraform-output", str(src), "--output", str(dst)]), 0)
            self.assertIn("qdrant_bootstrap_private_ip: 10.2.0.11", dst.read_text(encoding="utf-8"))

    def test_terraform_template_declares_the_same_groups(self) -> None:
        template = TEMPLATE.read_text(encoding="utf-8")
        for group in ("qdrant:", "qdrant_bootstrap:", "qdrant_monitoring:", "qdrant_backup_coordinator:"):
            self.assertIn(group, template)
        for var in ("qdrant_node_id", "qdrant_private_ip", "qdrant_data_dir", "qdrant_bootstrap_private_ip"):
            self.assertIn(var, template)


if __name__ == "__main__":
    unittest.main()
