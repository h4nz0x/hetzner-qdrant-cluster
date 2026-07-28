#!/usr/bin/env python3
"""Regression tests for the read-only Qdrant live inventory audit."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "qdrant-live-audit.py"
WORKFLOW = ROOT / ".github/workflows/production-qdrant-live-audit.yml"
SPEC = importlib.util.spec_from_file_location("qdrant_live_audit", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


def server(name: str, private_ip: str, server_type: str) -> dict:
    return {
        "id": abs(hash(name)) % 100_000,
        "name": name,
        "server_type": {"name": server_type},
        "datacenter": {"location": {"name": "hil"}},
        "private_net": [{"network": 50, "ip": private_ip, "alias_ips": []}],
        "labels": {},
    }


def volume(name: str, size_gb: int, attached_to: str) -> dict:
    return {
        "id": abs(hash(name)) % 100_000,
        "name": name,
        "size": size_gb,
        "location": {"name": "hil"},
        "server": {"name": attached_to},
    }


def load_balancer(name: str, private_ip: str, ports: list[int]) -> dict:
    return {
        "id": abs(hash(name)) % 100_000,
        "name": name,
        "load_balancer_type": {"name": "lb11"},
        "location": {"name": "hil"},
        "private_net": [{"network": 50, "ip": private_ip}],
        "services": [{"listen_port": port} for port in ports],
    }


def live_inventory() -> dict:
    return {
        "servers": [
            server("qdrant-node-1", "10.2.0.2", "ccx43"),
            server("qdrant-node-2", "10.2.0.3", "ccx43"),
            server("qdrant-node-3", "10.2.0.5", "ccx33"),
            server("qdrant-nginx-proxy", "10.2.0.6", "cpx11"),
        ],
        "volumes": [
            volume("qdrant-vol-1", 512, "qdrant-node-1"),
            volume("qdrant-vol-2", 512, "qdrant-node-2"),
            volume("qdrant-vol-3", 512, "qdrant-node-3"),
        ],
        "load_balancers": [
            load_balancer("qdrant-lb", "10.2.0.4", [443, 6333]),
            load_balancer("qdrant-grpc-lb", "10.2.0.7", [6334]),
            load_balancer("qdrant-metrics-lb", "10.2.0.8", [6336]),
        ],
        "networks": [{"id": 50, "name": "qdrant", "ip_range": "10.2.0.0/16"}],
        "firewalls": [{"id": 60, "name": "qdrant-private"}],
    }


class QdrantLiveAuditTest(unittest.TestCase):
    def test_workflow_is_manual_main_only_and_production_protected(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        for required in (
            "workflow_dispatch:",
            "READ_ONLY_QDRANT_LIVE_AUDIT",
            "github.ref == 'refs/heads/main'",
            "environment: production",
            "HCLOUD_TOKEN: ${{ secrets.HCLOUD_TOKEN }}",
            "python3 tests/qdrant-live-audit-test.py",
            "python3 scripts/qdrant-live-audit.py",
            "production-qdrant-live-audit-${{ github.run_id }}",
        ):
            self.assertIn(required, workflow)

        forbidden = (
            "terraform apply",
            "ansible-playbook",
            "docker compose",
            "ssh ",
            "qdrant-client",
        )
        for phrase in forbidden:
            self.assertNotIn(phrase, workflow)

    def test_script_describes_read_only_boundary(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("does not SSH to hosts", script)
        self.assertIn("or write to Qdrant", script)

    def build_report(self, inventory: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "hcloud.json"
            output_json = Path(tmp) / "audit.json"
            output_md = Path(tmp) / "audit.md"
            fixture.write_text(json.dumps(inventory), encoding="utf-8")
            status = AUDIT.main(
                [
                    "--hcloud-json",
                    str(fixture),
                    "--output-json",
                    str(output_json),
                    "--output-md",
                    str(output_md),
                ]
            )
            self.assertEqual(status, 0)
            self.assertIn("Qdrant live inventory audit", output_md.read_text(encoding="utf-8"))
            return json.loads(output_json.read_text(encoding="utf-8"))

    def test_clean_fixture_has_no_findings(self) -> None:
        report = self.build_report(live_inventory())
        self.assertEqual(report["findings"], [])

    def test_missing_server_is_high(self) -> None:
        inventory = live_inventory()
        inventory["servers"] = [
            item for item in inventory["servers"] if item["name"] != "qdrant-node-3"
        ]
        messages = {item["message"] for item in self.build_report(inventory)["findings"]}
        self.assertIn("Expected Qdrant server is missing live.", messages)

    def test_private_ip_drift_is_high(self) -> None:
        inventory = live_inventory()
        for item in inventory["servers"]:
            if item["name"] == "qdrant-node-2":
                item["private_net"][0]["ip"] = "10.2.0.30"
        messages = {item["message"] for item in self.build_report(inventory)["findings"]}
        self.assertIn("Expected Qdrant server private IP is missing live.", messages)

    def test_volume_size_and_attachment_drift_are_high(self) -> None:
        inventory = live_inventory()
        for item in inventory["volumes"]:
            if item["name"] == "qdrant-vol-1":
                item["size"] = 256
            if item["name"] == "qdrant-vol-2":
                item["server"]["name"] = "qdrant-node-1"
        messages = {item["message"] for item in self.build_report(inventory)["findings"]}
        self.assertIn("Qdrant volume size differs from inventory.", messages)
        self.assertIn("Qdrant volume is attached to the wrong server.", messages)

    def test_missing_load_balancer_is_high(self) -> None:
        inventory = live_inventory()
        inventory["load_balancers"] = [
            item for item in inventory["load_balancers"] if item["name"] != "qdrant-grpc-lb"
        ]
        messages = {item["message"] for item in self.build_report(inventory)["findings"]}
        self.assertIn("Expected Qdrant load balancer is missing live.", messages)


if __name__ == "__main__":
    unittest.main()
