#!/usr/bin/env python3
"""Regression tests for Qdrant snapshot backup automation."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_SCRIPT = ROOT / "scripts" / "qdrant-backup-manifest.py"
RETENTION_SCRIPT = ROOT / "scripts" / "qdrant-s3-retention-plan.py"
S3_VERIFY_SCRIPT = ROOT / "scripts" / "qdrant-s3-object-verify.py"
STREAM_VERIFY_SCRIPT = ROOT / "scripts" / "qdrant-stream-verify.py"
CLEANUP_REPORT_SCRIPT = ROOT / "scripts" / "qdrant-local-cleanup-report.py"
METRICS_SCRIPT = ROOT / "scripts" / "qdrant-backup-metrics.py"
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
S3_VERIFY = load_module(S3_VERIFY_SCRIPT, "qdrant_s3_object_verify")
STREAM_VERIFY = load_module(STREAM_VERIFY_SCRIPT, "qdrant_stream_verify")
CLEANUP = load_module(CLEANUP_REPORT_SCRIPT, "qdrant_local_cleanup_report")
METRICS = load_module(METRICS_SCRIPT, "qdrant_backup_metrics")


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
                "checksum": "a" * 64,
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
        self.assertTrue(all(item["size"] == 1024 for item in upload_plan))
        self.assertTrue(all(item["checksum"] == "a" * 64 for item in upload_plan))
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

    def test_manifest_rejects_missing_or_malformed_sha256(self) -> None:
        backup_id = "2026-07-29T02:30:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            node_files = []
            for node in ("qdrant-node-1", "qdrant-node-2", "qdrant-node-3"):
                payload = node_payload(node, backup_id)
                payload["collections"][0]["checksum"] = "not-a-sha256"
                path = Path(tmp) / f"{node}.json"
                path.write_text(json.dumps(payload), encoding="utf-8")
                node_files.append(path)
            with self.assertRaises(MANIFEST.ManifestError):
                MANIFEST.build_manifest(node_files, backup_id, "bucket", "prefix")

    def test_retention_keeps_latest_two_and_protects_new_backup(self) -> None:
        payload = {
            "Contents": [
                {"Key": "qdrant/production/2026-07-30T02:30:00Z/nodes/qdrant-node-1/partial.snapshot"},
                {"Key": "qdrant/production/2026-07-29T02:30:00Z/manifest.json"},
                {"Key": "qdrant/production/2026-07-28T14:30:00Z/manifest.json"},
                {"Key": "qdrant/production/2026-07-28T02:30:00Z/manifest.json"},
                {"Key": "qdrant/production/2026-07-27T14:30:00Z/manifest.json"},
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
            "Contents": [
                {"Key": "qdrant/production/2026-07-29T02:30:00Z/manifest.json"},
                {"Key": "qdrant/production/2026-07-28T14:30:00Z/manifest.json"},
                {"Key": "qdrant/production/2026-07-28T02:30:00Z/manifest.json"},
            ]
        }
        with self.assertRaises(RETENTION.RetentionError):
            RETENTION.select_delete_prefixes(payload, 2, "2026-07-28T02:30:00Z")

    def test_s3_object_verifier_requires_size_checksum_and_encryption(self) -> None:
        checksum = "a" * 64
        payload = {
            "ContentLength": 1024,
            "Metadata": {"qdrant-sha256": checksum},
            "ChecksumSHA256": "s3-validated-checksum",
            "ETag": '"etag-value"',
            "ServerSideEncryption": "AES256",
        }
        result = S3_VERIFY.verify_head_object(payload, 1024, checksum)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["content_length"], 1024)

        for changed in (
            {**payload, "ContentLength": 1023},
            {**payload, "Metadata": {"qdrant-sha256": "b" * 64}},
            {**payload, "ChecksumSHA256": ""},
            {**payload, "ServerSideEncryption": None},
        ):
            with self.assertRaises(S3_VERIFY.VerificationError):
                S3_VERIFY.verify_head_object(changed, 1024, checksum)

    def test_snapshot_stream_must_match_manifest_before_upload_can_succeed(self) -> None:
        content = b"immutable qdrant snapshot bytes"
        checksum = hashlib.sha256(content).hexdigest()
        destination = io.BytesIO()
        result = STREAM_VERIFY.copy_and_verify(
            io.BytesIO(content), destination, len(content), checksum
        )
        self.assertEqual(destination.getvalue(), content)
        self.assertEqual(result["sha256"], checksum)

        with self.assertRaises(STREAM_VERIFY.StreamVerificationError):
            STREAM_VERIFY.copy_and_verify(
                io.BytesIO(content), io.BytesIO(), len(content), "b" * 64
            )

    def test_cleanup_report_requires_exact_deletions_and_zero_remaining(self) -> None:
        upload_plan = [
            {
                "node": f"qdrant-node-{number}",
                "collection": "vectors",
                "snapshot_name": f"snapshot-{number}",
                "size": 1024,
                "checksum": "a" * 64,
            }
            for number in (1, 2, 3)
        ]
        manifest = {"backup_id": "2026-07-29T02:30:00Z", "upload_plan": upload_plan}
        deletions = [
            {
                **item,
                "deleted": True,
                "validated_size": item["size"],
                "validated_sha256": item["checksum"],
                "remaining_collection_snapshots": 0,
            }
            for item in upload_plan
        ]
        report = CLEANUP.build_cleanup_report(manifest, deletions)
        self.assertEqual(report["status"], "succeeded")
        self.assertEqual(report["deleted_snapshots"], 3)
        self.assertEqual(report["remaining_snapshots"], 0)

        with self.assertRaises(CLEANUP.CleanupError):
            CLEANUP.build_cleanup_report(manifest, deletions[:-1])

        deletions[0]["remaining_collection_snapshots"] = 1
        with self.assertRaises(CLEANUP.CleanupError):
            CLEANUP.build_cleanup_report(manifest, deletions)

    def test_metrics_publish_cleanup_success_and_transition_unknown(self) -> None:
        backup_id = "2026-07-29T02:30:00Z"
        with tempfile.TemporaryDirectory() as tmp:
            node_files = []
            for node in ("qdrant-node-1", "qdrant-node-2", "qdrant-node-3"):
                path = Path(tmp) / f"{node}.json"
                path.write_text(json.dumps(node_payload(node, backup_id)), encoding="utf-8")
                node_files.append(path)
            manifest, upload_plan = MANIFEST.build_manifest(
                node_files, backup_id, "bucket", "production"
            )

        cleanup = {
            "backup_id": backup_id,
            "status": "succeeded",
            "expected_snapshots": len(upload_plan),
            "deleted_snapshots": len(upload_plan),
            "remaining_snapshots": 0,
        }
        healthy = "\n".join(
            METRICS.success_metrics(manifest, cleanup, "qdrant", "production", 2)
        )
        self.assertIn(
            'qdrant_backup_local_snapshot_cleanup_success{cluster="qdrant",environment="production"} 1',
            healthy,
        )
        self.assertIn(
            'qdrant_backup_local_snapshots_remaining{cluster="qdrant",environment="production"} 0',
            healthy,
        )

        transition = "\n".join(
            METRICS.success_metrics(manifest, None, "qdrant", "production", 2)
        )
        self.assertIn('qdrant_backup_local_snapshots_remaining{cluster="qdrant",environment="production"} -1', transition)

    def test_node_script_uses_guarded_qdrant_snapshot_delete(self) -> None:
        script = NODE_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("/collections/{encoded}/snapshots?wait=true", script)
        self.assertIn("/collections/${encoded_collection}/snapshots/${encoded_snapshot}", script)
        self.assertIn('qdrant_curl_max_time="${QDRANT_CURL_MAX_TIME:-120}"', script)
        self.assertIn('qdrant_curl_max_time="${QDRANT_DOWNLOAD_MAX_TIME:-840}"', script)
        self.assertIn("delete)", script)
        self.assertIn("local snapshot size does not match the backup manifest", script)
        self.assertIn("local snapshot checksum does not match the backup manifest", script)
        self.assertIn(
            'curl_qdrant DELETE "/collections/${encoded_collection}/snapshots/${encoded_snapshot}?wait=true"',
            script,
        )
        self.assertIn("local snapshot is still present after Qdrant deletion", script)
        for forbidden in ("snapshots/delete", "rm -rf", "docker", "DELETE /collections"):
            self.assertNotIn(forbidden, script)

    def test_node_delete_refuses_mismatch_before_calling_qdrant_delete(self) -> None:
        checksum = "a" * 64
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_path = tmp_path / "state.json"
            fake_curl = tmp_path / "curl"
            fake_curl.write_text(
                """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

state_path = Path(os.environ["FAKE_QDRANT_STATE"])
state = {"deleted": False, "delete_calls": 0}
if state_path.exists():
    state = json.loads(state_path.read_text(encoding="utf-8"))
method = sys.argv[sys.argv.index("--request") + 1]
url = sys.argv[-1]
checksum = "a" * 64
if method == "GET" and url.endswith("/collections/vectors/snapshots"):
    snapshots = [] if state["deleted"] else [
        {"name": "vectors.snapshot", "size": 1024, "checksum": checksum}
    ]
    response = {"status": "ok", "result": snapshots}
elif method == "DELETE" and url.endswith(
    "/collections/vectors/snapshots/vectors.snapshot?wait=true"
):
    state["delete_calls"] += 1
    state["deleted"] = True
    response = {"status": "ok", "result": True}
else:
    raise SystemExit(f"unexpected fake curl request: {method} {url}")
state_path.write_text(json.dumps(state), encoding="utf-8")
print(json.dumps(response))
""",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)
            env = {
                **os.environ,
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "FAKE_QDRANT_STATE": str(state_path),
                "QDRANT_LOCAL_URL": "http://qdrant.test",
                "QDRANT_API_KEY": "test-api-key",
            }
            base_command = [
                "bash",
                str(NODE_SCRIPT),
                "delete",
                "qdrant-node-1",
                "vectors",
                "vectors.snapshot",
                "1024",
            ]
            mismatch = subprocess.run(
                [*base_command, "b" * 64],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(mismatch.returncode, 0)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["delete_calls"], 0)
            self.assertFalse(state["deleted"])

            matched = subprocess.run(
                [*base_command, checksum],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            result = json.loads(matched.stdout)
            self.assertTrue(result["deleted"])
            self.assertEqual(result["remaining_collection_snapshots"], 0)
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["delete_calls"], 1)

    def test_workflow_runs_protected_manual_backup_only(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for required in (
            "workflow_dispatch:",
            "CREATE_QDRANT_S3_SNAPSHOT_BACKUP",
            "environment: production",
            "QDRANT_SSH_PRIVATE_KEY: ${{ secrets.QDRANT_SSH_PRIVATE_KEY }}",
            "QDRANT_BACKUP_AWS_ACCESS_KEY_ID",
            "QDRANT_BACKUP_AWS_SECRET_ACCESS_KEY",
            "QDRANT_BACKUP_S3_BUCKET",
            "QDRANT_BACKUP_S3_PREFIX",
            "python3 tests/qdrant-backup-test.py",
            "scripts/qdrant-backup-node.sh",
            "scripts/qdrant-s3-object-verify.py",
            "scripts/qdrant-stream-verify.py",
            "scripts/qdrant-local-cleanup-report.py",
            "scripts/qdrant-snapshot-backup.sh",
            "shellcheck --severity=warning scripts/qdrant-snapshot-backup.sh",
            "bash scripts/qdrant-snapshot-backup.sh",
        ):
            self.assertIn(required, workflow)

        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("backup-scheduled:", workflow)
        self.assertNotIn("github.event_name == 'schedule'", workflow)

        script = (ROOT / "scripts" / "qdrant-snapshot-backup.sh").read_text(encoding="utf-8")
        for required in (
            "QDRANT_SSH_PRIVATE_KEY",
            "scripts/qdrant-backup-manifest.py",
            "scripts/qdrant-s3-retention-plan.py",
            "scripts/qdrant-stream-verify.py",
            "--checksum-algorithm SHA256",
            "--checksum-mode ENABLED",
            "qdrant-sha256=",
            "${remote_cmd} delete",
            "--keep 2",
        ):
            self.assertIn(required, script)

        self.assertLess(script.index("--checksum-mode ENABLED"), script.index("${remote_cmd} delete"))

        for forbidden in ("ansible-playbook", "terraform apply", "docker compose"):
            self.assertNotIn(forbidden, workflow)
            self.assertNotIn(forbidden, script)

        runbook = RUNBOOK.read_text(encoding="utf-8")
        for required in (
            "manual backup",
            "systemd timer",
            "latest two completed backup sets",
            "local snapshots",
            "qdrant-local-snapshot-cleanup.json",
        ):
            self.assertIn(required, runbook)


if __name__ == "__main__":
    unittest.main()
