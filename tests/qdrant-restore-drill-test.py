#!/usr/bin/env python3
"""Regression tests for Qdrant disposable restore drill automation."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESTORE_SCRIPT = ROOT / "scripts" / "qdrant-restore-drill.py"
WORKFLOW = ROOT / ".github/workflows/production-qdrant-restore-drill.yml"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RESTORE = load_module(RESTORE_SCRIPT, "qdrant_restore_drill")


def manifest_payload() -> dict:
    backup_id = "2026-07-29T10:27:49Z"
    return {
        "backup_id": backup_id,
        "bucket": "qdrant-backups-example",
        "prefix": "production",
        "nodes": [
            {
                "node": node,
                "collections": [
                    {
                        "collection": "example_collection",
                        "snapshot_name": f"example_collection-{node}.snapshot",
                        "size": 1234,
                        "checksum": "abc123",
                        "creation_time": "2026-07-29T10:27:49",
                    }
                ],
            }
            for node in ("qdrant-node-1", "qdrant-node-2", "qdrant-node-3")
        ],
    }


class QdrantRestoreDrillTest(unittest.TestCase):
    def test_plan_selects_manifest_snapshot_without_live_access(self) -> None:
        backup_id = "2026-07-29T10:27:49Z"
        plan = RESTORE.select_snapshot(
            manifest_payload(),
            backup_id,
            "qdrant-node-1",
            "example_collection",
            Path("/tmp/snapshots"),
        )
        self.assertEqual(plan["backup_id"], backup_id)
        self.assertEqual(plan["bucket"], "qdrant-backups-example")
        self.assertEqual(plan["collection"], "example_collection")
        self.assertEqual(
            plan["s3_key"],
            "production/2026-07-29T10:27:49Z/nodes/qdrant-node-1/"
            "collections/example_collection/example_collection-qdrant-node-1.snapshot",
        )
        self.assertEqual(
            plan["qdrant_file_uri"],
            "file:///qdrant/snapshots/example_collection/"
            "example_collection-qdrant-node-1.snapshot",
        )

    def test_plan_rejects_bad_backup_id_and_missing_node(self) -> None:
        manifest = manifest_payload()
        with self.assertRaises(RESTORE.RestoreDrillError):
            RESTORE.select_snapshot(
                manifest,
                "2026-07-29",
                "qdrant-node-1",
                None,
                Path("/tmp/snapshots"),
            )

        manifest["nodes"] = manifest["nodes"][:2]
        with self.assertRaises(RESTORE.RestoreDrillError):
            RESTORE.select_snapshot(
                manifest,
                "2026-07-29T10:27:49Z",
                "qdrant-node-1",
                None,
                Path("/tmp/snapshots"),
            )

    def test_verify_evidence_requires_ready_collection_and_sample_point(self) -> None:
        self.assertEqual(RESTORE.collection_status({"result": {"status": "green"}}), "green")
        self.assertEqual(RESTORE.collection_points({"result": {"points_count": 42}}), 42)
        self.assertEqual(RESTORE.scroll_count({"result": {"points": [{"id": 1}]}}), 1)
        self.assertEqual(RESTORE.scroll_count({"result": {"points": []}}), 0)

        summary = RESTORE.render_verify_summary(
            {
                "passed": True,
                "collection": "example_collection",
                "api_status": "ok",
                "collection_status": "green",
                "points_count": 42,
                "sample_points_returned": 1,
            }
        )
        self.assertIn("API status", summary)
        self.assertNotIn("Readiness", summary)

    def test_cli_plan_writes_sanitized_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            manifest = tmp_path / "manifest.json"
            output_json = tmp_path / "plan.json"
            output_md = tmp_path / "plan.md"
            manifest.write_text(json.dumps(manifest_payload()), encoding="utf-8")

            result = RESTORE.main(
                [
                    "plan",
                    "--manifest",
                    str(manifest),
                    "--backup-id",
                    "2026-07-29T10:27:49Z",
                    "--snapshot-dir",
                    str(tmp_path / "snapshots"),
                    "--output-json",
                    str(output_json),
                    "--output-md",
                    str(output_md),
                ]
            )

            self.assertEqual(result, 0)
            plan = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(plan["source_node"], "qdrant-node-1")
            self.assertIn("Qdrant restore drill plan", output_md.read_text(encoding="utf-8"))

    def test_workflow_is_manual_disposable_and_never_targets_live_qdrant(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "workflow_dispatch:",
            "RUN_QDRANT_RESTORE_DRILL",
            "environment: production",
            "qdrant/qdrant:v1.17.0",
            "docker run",
            '${restore_url}/collections',
            "snapshots/recover?wait=true",
            '"priority": "snapshot"',
            "recover-response.json",
            "recover-status.txt",
            "snapshot recovery failed with HTTP",
            "curl exit",
            "if: always()",
            "if-no-files-found: warn",
            'chmod 0777 "$snapshot_dir"',
            '--volume "${QDRANT_RESTORE_SNAPSHOT_DIR}:/qdrant/snapshots"',
            "alpine:3.20",
            "/cleanup-target",
            "scripts/qdrant-restore-drill.py",
            "python3 tests/qdrant-restore-drill-test.py",
            "production-qdrant-restore-drill-${{ github.run_id }}",
        ):
            self.assertIn(required, workflow)

        for forbidden in (
            "QDRANT_SSH_PRIVATE_KEY",
            "ssh ",
            "203.0.113.",
            "10.2.0.",
            "terraform apply",
            "ansible-playbook",
            "docker compose",
            ':/qdrant/snapshots:ro',
            "${restore_url}/readiness",
        ):
            self.assertNotIn(forbidden, workflow)


if __name__ == "__main__":
    unittest.main()
