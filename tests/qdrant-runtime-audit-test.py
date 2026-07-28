#!/usr/bin/env python3
"""Regression tests for the read-only Qdrant runtime audit."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_SCRIPT = ROOT / "scripts" / "qdrant-runtime-report.py"
READ_SCRIPT = ROOT / "scripts" / "qdrant-runtime-read.sh"
WORKFLOW = ROOT / ".github/workflows/production-qdrant-runtime-audit.yml"
SPEC = importlib.util.spec_from_file_location("qdrant_runtime_report", REPORT_SCRIPT)
assert SPEC and SPEC.loader
REPORT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = REPORT
SPEC.loader.exec_module(REPORT)


def inspect_payload(image: str, running: bool = True) -> list[dict]:
    return [
        {
            "Config": {"Image": image, "Env": ["QDRANT_API_KEY=redacted"]},
            "State": {"Running": running, "Status": "running" if running else "exited"},
            "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
            "Mounts": [{"Destination": "/qdrant/storage"}],
        }
    ]


def node_payload(
    name: str,
    *,
    qdrant_image: str = "qdrant/qdrant:v1.17.0",
    qdrant_running: bool = True,
    readiness: str = "ok",
    peers: int = 3,
    replication_factor: int = 2,
    snapshot_count: int = 1,
    storage_used_percent: int = 20,
) -> dict:
    total = 512 * 1024**3
    used = int(total * (storage_used_percent / 100))
    return {
        "node": name,
        "hostname": name,
        "qdrant_container": inspect_payload(qdrant_image, qdrant_running),
        "metrics_proxy_container": inspect_payload("nginx:1.25-alpine", True),
        "readiness": {"status": readiness},
        "cluster": {"result": {"peers": {str(index): {} for index in range(peers)}}},
        "collections": [
            {
                "name": "prod_vectors",
                "status": "green",
                "replication_factor": replication_factor,
                "write_consistency_factor": 1,
                "points_count": 10,
                "snapshot_count": snapshot_count,
            }
        ],
        "storage": {
            "path": "/qdrant/storage",
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": total - used,
        },
    }


class QdrantRuntimeAuditTest(unittest.TestCase):
    def build_report(self, payloads: list[dict]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            node_files = []
            for payload in payloads:
                path = Path(tmp) / f"{payload['node']}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                node_files.extend(["--node-json", str(path)])

            output_json = Path(tmp) / "report.json"
            output_md = Path(tmp) / "report.md"
            status = REPORT.main(
                [
                    *node_files,
                    "--output-json",
                    str(output_json),
                    "--output-md",
                    str(output_md),
                ]
            )
            self.assertEqual(status, 0)
            self.assertIn("Qdrant runtime audit", output_md.read_text(encoding="utf-8"))
            return json.loads(output_json.read_text(encoding="utf-8"))

    def test_clean_fixture_has_no_findings_and_redacts_env(self) -> None:
        report = self.build_report(
            [
                node_payload("qdrant-node-1"),
                node_payload("qdrant-node-2"),
                node_payload("qdrant-node-3"),
            ]
        )
        self.assertEqual(report["findings"], [])
        serialized = json.dumps(report)
        self.assertNotIn("QDRANT_API_KEY=redacted", serialized)
        self.assertIn("qdrant/qdrant:v1.17.0", serialized)

    def test_bad_runtime_state_creates_findings(self) -> None:
        report = self.build_report(
            [
                node_payload("qdrant-node-1", qdrant_running=False),
                node_payload("qdrant-node-2", readiness="failed", peers=2),
                node_payload(
                    "qdrant-node-3",
                    qdrant_image="qdrant/qdrant:v1.18.0",
                    replication_factor=1,
                    snapshot_count=0,
                    storage_used_percent=90,
                ),
            ]
        )
        messages = {item["message"] for item in report["findings"]}
        self.assertIn("Qdrant container is not running.", messages)
        self.assertIn("Qdrant readiness did not report ok.", messages)
        self.assertIn(
            "Qdrant cluster peer count is below the expected three nodes.",
            messages,
        )
        self.assertIn("Qdrant image tag differs from the reviewed baseline.", messages)
        self.assertIn("Qdrant collection replication is below the HA baseline.", messages)
        self.assertIn("Qdrant collection has no visible snapshots on this node.", messages)
        self.assertIn("Qdrant storage usage is high.", messages)

    def test_missing_expected_node_is_high(self) -> None:
        report = self.build_report([node_payload("qdrant-node-1"), node_payload("qdrant-node-2")])
        messages = {item["message"] for item in report["findings"]}
        self.assertIn("Expected Qdrant runtime node is missing.", messages)

    def test_read_script_has_no_mutating_commands(self) -> None:
        script = READ_SCRIPT.read_text(encoding="utf-8")
        forbidden = (
            "docker restart",
            "docker stop",
            "docker compose up",
            "docker compose down",
            "snapshots/delete",
            "PUT ",
            "POST ",
            "DELETE ",
        )
        for phrase in forbidden:
            self.assertNotIn(phrase, script)

    def test_workflow_is_manual_protected_and_read_only(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "workflow_dispatch:",
            "READ_ONLY_QDRANT_RUNTIME_AUDIT",
            "github.ref == 'refs/heads/main'",
            "environment: production",
            "QDRANT_SSH_PRIVATE_KEY: ${{ secrets.QDRANT_SSH_PRIVATE_KEY }}",
            "python3 tests/qdrant-runtime-audit-test.py",
            "scripts/qdrant-runtime-read.sh",
            "scripts/qdrant-runtime-report.py",
            "production-qdrant-runtime-audit-${{ github.run_id }}",
        ):
            self.assertIn(required, workflow)

        for forbidden in (
            "ansible-playbook",
            "terraform apply",
            "docker compose up",
            "secrets.QDRANT_API_KEY",
        ):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
