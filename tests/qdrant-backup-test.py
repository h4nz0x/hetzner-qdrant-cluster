#!/usr/bin/env python3
"""Regression tests for Qdrant snapshot backup automation."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCRIPT = ROOT / "scripts" / "qdrant-backup-manifest.py"
RETENTION_SCRIPT = ROOT / "scripts" / "qdrant-s3-retention-plan.py"
NODE_SCRIPT = ROOT / "scripts" / "qdrant-backup-node.sh"
RUNBOOK = ROOT / "docs" / "runbooks" / "qdrant-snapshot-backup.md"
WORKFLOW = ROOT / ".github/workflows/production-qdrant-snapshot-backup.yml"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


MANIFEST = load_module(MANIFEST_SCRIPT, "qdrant_backup_manifest")
RETENTION = load_module(RETENTION_SCRIPT, "qdrant_s3_retention_plan")


def node_payload(node: str, backup_id: str, collections: list[str] | None = None) -> dict:
    collections = collections or ["prod_vectors", "tenant_vectors"]
    return {
        "node": node,
        "backup_id": backup_id,
        "collections": [
            {
                "collection": collection,
                "snapshot_name": f"{collection}-{node}-{backup_id}.snapshot",
                "size": 1024,
                "checksum": "abc123",
                "creation_time": "2026-07-29T02:30:00",
            }
            for collection in collections
        ],
    }


class QdrantBackupTest(unittest.TestCase):
    def test_manifest_requires_three_nodes_and_builds_upload_plan(self) -> None:
        backup_id = "2026-07-29T02:30:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            node_files = []
            for node in ("qdrant-node-1", "qdrant-node-2", "qdrant-node-3"):
                path = Path(tmp) / f"{node}.json"
                path.write_text(json.dumps(node_payload(node, backup_id)), encoding="utf-8")
                node_files.append(path)

            manifest, upload_plan = MANIFEST.build_manifest(
                node_files,
                backup_id,
                "qdrant-backups",
                "qdrant/production",
            )

        self.assertEqual(manifest["backup_id"], backup_id)
        self.assertEqual(manifest["manifest_key"], f"qdrant/production/{backup_id}/manifest.json")
        self.assertEqual(len(upload_plan), 6)
        self.assertTrue(
            all(
                item["s3_key"].startswith(f"qdrant/production/{backup_id}/")
                for item in upload_plan
            )
        )

    def test_manifest_rejects_missing_node_and_collection_mismatch(self) -> None:
        backup_id = "2026-07-29T02:30:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            one = Path(tmp) / "one.json"
            two = Path(tmp) / "two.json"
            three = Path(tmp) / "three.json"
            one.write_text(json.dumps(node_payload("qdrant-node-1", backup_id)), encoding="utf-8")
            two.write_text(json.dumps(node_payload("qdrant-node-2", backup_id)), encoding="utf-8")
            with self.assertRaises(MANIFEST.ManifestError):
                MANIFEST.build_manifest([one, two], backup_id, "bucket", "prefix")

            three.write_text(
                json.dumps(node_payload("qdrant-node-3", backup_id, ["different"])),
                encoding="utf-8",
            )
            with self.assertRaises(MANIFEST.ManifestError):
                MANIFEST.build_manifest([one, two, three], backup_id, "bucket", "prefix")

    def test_retention_keeps_latest_two_and_protects_new_backup(self) -> None:
        payload = {
            "CommonPrefixes": [
                {"Prefix": "qdrant/production/2026-07-29T02:30:00Z/"},
                {"Prefix": "qdrant/production/2026-07-28T14:30:00Z/"},
                {"Prefix": "qdrant/production/2026-07-28T02:30:00Z/"},
                {"Prefix": "qdrant/production/2026-07-27T14:30:00Z/"},
            ]
        }
        delete = RETENTION.select_delete_prefixes(payload, 2, "2026-07-29T02:30:00Z")
        self.assertEqual(
            delete,
            [
                "qdrant/production/2026-07-28T02:30:00Z/",
                "qdrant/production/2026-07-27T14:30:00Z/",
            ],
        )

    def test_retention_refuses_when_protected_backup_would_not_be_retained(self) -> None:
        payload = {
            "CommonPrefixes": [
                {"Prefix": "qdrant/production/2026-07-29T02:30:00Z/"},
                {"Prefix": "qdrant/production/2026-07-28T14:30:00Z/"},
                {"Prefix": "qdrant/production/2026-07-28T02:30:00Z/"},
            ]
        }
        with self.assertRaises(RETENTION.RetentionError):
            RETENTION.select_delete_prefixes(payload, 2, "2026-07-28T02:30:00Z")

    def test_node_script_creates_and_downloads_but_never_deletes(self) -> None:
        script = NODE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("/collections/{encoded}/snapshots?wait=true", script)
        self.assertIn("/collections/${encoded_collection}/snapshots/${encoded_snapshot}", script)
        self.assertIn('qdrant_curl_max_time="${QDRANT_CURL_MAX_TIME:-120}"', script)
        self.assertIn('qdrant_curl_max_time="${QDRANT_DOWNLOAD_MAX_TIME:-840}"', script)
        for forbidden in ("DELETE", "snapshots/delete", "rm -rf", "docker"):
            self.assertNotIn(forbidden, script)

    def test_workflow_runs_manual_and_scheduled_backups_with_two_set_retention(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            'cron: "0 */12 * * *"',
            "workflow_dispatch:",
            "CREATE_QDRANT_S3_SNAPSHOT_BACKUP",
            "EVENT_NAME: ${{ github.event_name }}",
            "backup-manual:",
            "backup-scheduled:",
            "QDRANT_SSH_PRIVATE_KEY: ${{ secrets.QDRANT_SSH_PRIVATE_KEY }}",
            "QDRANT_BACKUP_AWS_ACCESS_KEY_ID",
            "QDRANT_BACKUP_AWS_SECRET_ACCESS_KEY",
            "QDRANT_BACKUP_S3_BUCKET",
            "QDRANT_BACKUP_S3_PREFIX",
            "python3 tests/qdrant-backup-test.py",
            "scripts/qdrant-backup-node.sh",
            "scripts/qdrant-snapshot-backup.sh",
            "shellcheck --severity=warning scripts/qdrant-snapshot-backup.sh",
            "bash scripts/qdrant-snapshot-backup.sh",
            "github.event_name == 'workflow_dispatch'",
            "github.event_name == 'schedule'",
        ):
            self.assertIn(required, workflow)

        manual_job = workflow.split("  backup-manual:", 1)[1].split("  backup-scheduled:", 1)[0]
        scheduled_job = workflow.split("  backup-scheduled:", 1)[1]
        self.assertIn("environment: production", manual_job)
        self.assertNotIn("environment:", scheduled_job)

        script = (ROOT / "scripts" / "qdrant-snapshot-backup.sh").read_text(encoding="utf-8")
        for required in (
            "QDRANT_SSH_PRIVATE_KEY",
            "scripts/qdrant-backup-manifest.py",
            "scripts/qdrant-s3-retention-plan.py",
            "--keep 2",
        ):
            self.assertIn(required, script)

        for forbidden in ("ansible-playbook", "terraform apply", "docker compose"):
            self.assertNotIn(forbidden, workflow)
            self.assertNotIn(forbidden, script)

        runbook = RUNBOOK.read_text(encoding="utf-8")
        for required in (
            "automatic production backup every 12 hours",
            "0 */12 * * *",
            "repository secrets for the scheduled path",
            "latest two completed backup sets",
        ):
            self.assertIn(required, runbook)


if __name__ == "__main__":
    unittest.main()
