#!/usr/bin/env python3
"""Build a sanitized Qdrant backup manifest and upload plan."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")
BACKUP_ID = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
SHA256 = re.compile(r"^[A-Fa-f0-9]{64}$")


class ManifestError(Exception):
    pass


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ManifestError(f"{path} must contain a JSON object")
    return payload


def normalize_prefix(prefix: str) -> str:
    return prefix.strip("/")


def require_safe(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise ManifestError(f"{label} must match {SAFE_NAME.pattern}; got {value!r}")
    return value


def require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ManifestError(f"{label} must be a 64-character SHA-256")
    return value.lower()


def build_manifest(
    node_json: list[Path],
    backup_id: str,
    bucket: str,
    prefix: str,
    expected_node_count: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not BACKUP_ID.fullmatch(backup_id):
        raise ManifestError("backup_id must be an exact UTC timestamp like 2026-07-29T02:30:00Z")

    prefix = normalize_prefix(prefix)
    if not prefix:
        raise ManifestError("S3 prefix must not be empty")
    if not bucket:
        raise ManifestError("S3 bucket must not be empty")

    by_node: dict[str, dict[str, Any]] = {}
    for path in node_json:
        payload = read_json(path)
        node = require_safe(payload.get("node"), "node")
        if node in by_node:
            raise ManifestError(f"duplicate node payload: {node}")
        by_node[node] = payload

    expected_nodes = tuple(sorted(by_node))
    if not expected_nodes:
        raise ManifestError("at least one node payload is required")
    if expected_node_count is not None and len(expected_nodes) != expected_node_count:
        raise ManifestError(
            f"expected {expected_node_count} node payloads, got {len(expected_nodes)}"
        )

    collections_by_node: dict[str, list[dict[str, Any]]] = {}
    upload_plan: list[dict[str, Any]] = []
    for node in expected_nodes:
        payload = by_node[node]
        if payload.get("backup_id") != backup_id:
            raise ManifestError(f"{node} backup_id does not match {backup_id}")
        collections = payload.get("collections")
        if not isinstance(collections, list) or not collections:
            raise ManifestError(f"{node} did not report any collection snapshots")

        normalized = []
        for item in collections:
            if not isinstance(item, dict):
                raise ManifestError(f"{node} reported a malformed snapshot item")
            collection = require_safe(item.get("collection"), "collection")
            snapshot_name = require_safe(item.get("snapshot_name"), "snapshot_name")
            size = item.get("size")
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                raise ManifestError(f"{node}/{collection} has invalid snapshot size")
            checksum = require_sha256(
                item.get("checksum"), f"{node}/{collection} checksum"
            )
            normalized.append(
                {
                    "collection": collection,
                    "snapshot_name": snapshot_name,
                    "size": size,
                    "checksum": checksum,
                    "creation_time": item.get("creation_time"),
                }
            )
            s3_key = (
                f"{prefix}/{backup_id}/nodes/{node}/collections/"
                f"{collection}/{snapshot_name}"
            )
            upload_plan.append(
                {
                    "node": node,
                    "collection": collection,
                    "snapshot_name": snapshot_name,
                    "s3_key": s3_key,
                    "size": size,
                    "checksum": checksum,
                }
            )
        collections_by_node[node] = sorted(normalized, key=lambda row: row["collection"])

    collection_sets = {
        node: {item["collection"] for item in items}
        for node, items in collections_by_node.items()
    }
    expected_collections = collection_sets[expected_nodes[0]]
    for node, names in collection_sets.items():
        if names != expected_collections:
            raise ManifestError(
                f"{node} collection set differs from {expected_nodes[0]}"
            )

    manifest_key = f"{prefix}/{backup_id}/manifest.json"
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backup_id": backup_id,
        "bucket": bucket,
        "prefix": prefix,
        "manifest_key": manifest_key,
        "nodes": [
            {"node": node, "collections": collections_by_node[node]}
            for node in expected_nodes
        ],
        "upload_plan": upload_plan,
    }
    return manifest, upload_plan


def render_summary(manifest: dict[str, Any]) -> str:
    lines = [
        "# Qdrant snapshot backup",
        "",
        f"- Backup ID: `{manifest['backup_id']}`",
        f"- S3 manifest: `s3://{manifest['bucket']}/{manifest['manifest_key']}`",
        f"- Nodes: {len(manifest['nodes'])}",
        f"- Snapshot objects: {len(manifest['upload_plan'])}",
        "",
        "## Collections",
        "",
    ]
    collections = sorted(
        {
            item["collection"]
            for node in manifest["nodes"]
            for item in node["collections"]
        }
    )
    for collection in collections:
        lines.append(f"- `{collection}`")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Qdrant backup manifest")
    parser.add_argument("--node-json", type=Path, action="append", required=True)
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--upload-tsv", type=Path, required=True)
    parser.add_argument(
        "--expected-node-count",
        type=int,
        help="Fail unless exactly this many node payloads were provided",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        manifest, upload_plan = build_manifest(
            args.node_json,
            args.backup_id,
            args.bucket,
            args.prefix,
            args.expected_node_count,
        )
    except ManifestError as exc:
        raise SystemExit(f"Qdrant backup manifest failed: {exc}") from exc

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.upload_tsv.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    args.output_md.write_text(render_summary(manifest), encoding="utf-8")
    args.upload_tsv.write_text(
        "".join(
            "\t".join(
                [
                    item["node"],
                    item["collection"],
                    item["snapshot_name"],
                    item["s3_key"],
                    str(item["size"]),
                    item["checksum"],
                ]
            )
            + "\n"
            for item in upload_plan
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
