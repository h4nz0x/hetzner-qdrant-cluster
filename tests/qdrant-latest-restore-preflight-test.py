#!/usr/bin/env python3
"""Regression tests for Qdrant latest restore preflight."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT_SCRIPT = ROOT / "scripts" / "qdrant-latest-restore-preflight.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PREFLIGHT = load_module(PREFLIGHT_SCRIPT, "qdrant_latest_restore_preflight")


def manifest_payload(backup_id: str, collections: list[str] | None = None) -> dict:
    collections = collections or ["example_collection", "tenant_vectors"]
    return {
        "backup_id": backup_id,
        "bucket": "qdrant-backups-example",
        "prefix": "production",
        "nodes": [
            {
                "node": node,
                "collections": [
                    {
                        "collection": collection,
                        "snapshot_name": f"{collection}-{node}-{backup_id}.snapshot",
                        "size": 1024,
                        "checksum": "abc123",
                        "creation_time": "2026-07-30T15:51:46",
                    }
                    for collection in collections
                ],
            }
            for node in ("qdrant-node-1", "qdrant-node-2", "qdrant-node-3")
        ],
    }


class QdrantLatestRestorePreflightTest(unittest.TestCase):
    def test_selects_newest_complete_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            older = root / "older.json"
            newest = root / "newest.json"
            older.write_text(
                json.dumps(manifest_payload("2026-07-30T03:51:46Z")),
                encoding="utf-8",
            )
            newest.write_text(
                json.dumps(manifest_payload("2026-07-30T15:51:46Z")),
                encoding="utf-8",
            )

            selected, rejected = PREFLIGHT.select_latest_complete([older, newest])

        self.assertEqual(selected["backup_id"], "2026-07-30T15:51:46Z")
        self.assertEqual(selected["selected_collection"], "example_collection")
        self.assertEqual(selected["total_selected_size"], 3072)
        self.assertEqual(rejected, [])

    def test_rejects_incomplete_newer_manifest_and_uses_older_complete_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            complete = root / "complete.json"
            incomplete = root / "incomplete.json"
            complete.write_text(
                json.dumps(manifest_payload("2026-07-30T03:51:46Z")),
                encoding="utf-8",
            )
            payload = manifest_payload("2026-07-30T15:51:46Z")
            payload["nodes"] = payload["nodes"][:2]
            incomplete.write_text(json.dumps(payload), encoding="utf-8")

            selected, rejected = PREFLIGHT.select_latest_complete(
                [complete, incomplete], expected_node_count=3
            )

        self.assertEqual(selected["backup_id"], "2026-07-30T03:51:46Z")
        self.assertEqual(len(rejected), 1)
        self.assertIn("missing nodes", rejected[0]["reason"])

    def test_requested_collection_must_exist_on_every_node(self) -> None:
        manifest = manifest_payload("2026-07-30T15:51:46Z")
        selected = PREFLIGHT.validate_manifest(manifest, "tenant_vectors")
        self.assertEqual(selected["selected_collection"], "tenant_vectors")

        manifest["nodes"][2]["collections"] = manifest["nodes"][2]["collections"][:1]
        with self.assertRaises(PREFLIGHT.PreflightError):
            PREFLIGHT.validate_manifest(manifest, "tenant_vectors")

    def test_cli_writes_restore_inputs_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "manifest.json"
            output_json = root / "preflight.json"
            output_md = root / "preflight.md"
            manifest.write_text(
                json.dumps(manifest_payload("2026-07-30T15:51:46Z")),
                encoding="utf-8",
            )

            result = PREFLIGHT.main(
                [
                    "--manifest",
                    str(manifest),
                    "--output-json",
                    str(output_json),
                    "--output-md",
                    str(output_md),
                ]
            )

            self.assertEqual(result, 0)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertTrue(payload["passed"])
            summary = output_md.read_text(encoding="utf-8")
            self.assertIn("backup_id=2026-07-30T15:51:46Z", summary)
            self.assertIn("collection=example_collection", summary)

    def test_node_count_is_taken_from_manifest_when_not_pinned(self) -> None:
        payload = manifest_payload("2026-07-30T15:51:46Z")
        payload["nodes"] = payload["nodes"][:2]
        selected = PREFLIGHT.validate_manifest(payload)
        self.assertEqual(len(selected["nodes"]), 2)
        with self.assertRaises(PREFLIGHT.PreflightError):
            PREFLIGHT.validate_manifest(payload, expected_node_count=3)

if __name__ == "__main__":
    unittest.main()
