#!/usr/bin/env python3
"""Select the latest complete Qdrant backup manifest for restore drills."""

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


class PreflightError(Exception):
    pass


def require_safe(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise PreflightError(f"{label} must match {SAFE_NAME.pattern}; got {value!r}")
    return value


def read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PreflightError(f"{path} must contain a JSON object")
    return payload


def collection_names(node: dict[str, Any]) -> set[str]:
    collections = node.get("collections")
    if not isinstance(collections, list) or not collections:
        raise PreflightError("manifest node has no collection snapshots")

    names: set[str] = set()
    for item in collections:
        if not isinstance(item, dict):
            raise PreflightError("manifest contains a malformed collection item")
        collection = require_safe(item.get("collection"), "collection")
        snapshot_name = require_safe(item.get("snapshot_name"), "snapshot_name")
        size = item.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise PreflightError(f"{collection}/{snapshot_name} has invalid size")
        names.add(collection)
    return names


def validate_manifest(
    manifest: dict[str, Any],
    requested_collection: str | None = None,
    expected_node_count: int | None = None,
) -> dict[str, Any]:
    backup_id = manifest.get("backup_id")
    if not isinstance(backup_id, str) or not BACKUP_ID.fullmatch(backup_id):
        raise PreflightError("manifest backup_id must be an exact UTC timestamp")

    bucket = manifest.get("bucket")
    prefix = manifest.get("prefix")
    if not isinstance(bucket, str) or not bucket:
        raise PreflightError("manifest bucket is required")
    if not isinstance(prefix, str) or not prefix.strip("/"):
        raise PreflightError("manifest prefix is required")

    nodes = manifest.get("nodes")
    if not isinstance(nodes, list):
        raise PreflightError("manifest nodes must be a list")

    by_node: dict[str, dict[str, Any]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise PreflightError("manifest contains a malformed node")
        node_name = require_safe(node.get("node"), "node")
        by_node[node_name] = node

    expected_nodes = list(by_node)
    if not expected_nodes:
        raise PreflightError("manifest has no nodes")
    if expected_node_count is not None and len(expected_nodes) != expected_node_count:
        raise PreflightError(
            f"manifest is missing nodes: expected {expected_node_count}, found {len(expected_nodes)}"
        )

    names_by_node = {
        node_name: collection_names(by_node[node_name])
        for node_name in expected_nodes
    }
    common_collections = set.intersection(*(names_by_node[node] for node in expected_nodes))
    if not common_collections:
        raise PreflightError("manifest has no collection snapshot present on every node")

    if requested_collection:
        requested_collection = require_safe(requested_collection, "collection")
        if requested_collection not in common_collections:
            raise PreflightError(
                f"collection {requested_collection} is not present on every node"
            )
        selected_collection = requested_collection
    else:
        selected_collection = sorted(common_collections)[0]

    selected_snapshots = []
    for node_name in expected_nodes:
        collections = by_node[node_name]["collections"]
        selected = next(
            item for item in collections if item["collection"] == selected_collection
        )
        selected_snapshots.append(
            {
                "node": node_name,
                "collection": selected_collection,
                "snapshot_name": selected["snapshot_name"],
                "size": selected["size"],
                "checksum": selected.get("checksum"),
                "creation_time": selected.get("creation_time"),
            }
        )

    return {
        "backup_id": backup_id,
        "bucket": bucket,
        "prefix": prefix.strip("/"),
        "selected_collection": selected_collection,
        "common_collections": sorted(common_collections),
        "nodes": expected_nodes,
        "snapshots": selected_snapshots,
        "total_selected_size": sum(item["size"] for item in selected_snapshots),
    }


def select_latest_complete(
    manifest_paths: list[Path],
    requested_collection: str | None = None,
    expected_node_count: int | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not manifest_paths:
        raise PreflightError("at least one manifest path is required")

    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for path in manifest_paths:
        try:
            manifest = read_manifest(path)
            selected = validate_manifest(manifest, requested_collection, expected_node_count)
            selected["source_manifest"] = str(path)
            accepted.append(selected)
        except (json.JSONDecodeError, OSError, PreflightError) as exc:
            rejected.append({"manifest": str(path), "reason": str(exc)})

    if not accepted:
        raise PreflightError("no complete Qdrant backup manifest was found")

    accepted.sort(key=lambda item: item["backup_id"], reverse=True)
    return accepted[0], rejected


def render_summary(selection: dict[str, Any], rejected: list[dict[str, str]]) -> str:
    lines = [
        "# Qdrant latest restore preflight",
        "",
        f"- Result: `passed`",
        f"- Selected backup ID: `{selection['backup_id']}`",
        f"- Restore drill collection: `{selection['selected_collection']}`",
        f"- S3 prefix: `s3://{selection['bucket']}/{selection['prefix']}/`",
        f"- Nodes: {len(selection['nodes'])}",
        f"- Selected snapshot total size: {selection['total_selected_size']} bytes",
        "",
        "## Restore drill inputs",
        "",
        "```text",
        f"backup_id={selection['backup_id']}",
        f"collection={selection['selected_collection']}",
        "```",
        "",
    ]
    if rejected:
        lines += [
            "## Rejected manifests",
            "",
        ]
        for item in rejected:
            lines.append(f"- `{item['manifest']}`: {item['reason']}")
        lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select latest Qdrant restore backup")
    parser.add_argument("--manifest", type=Path, action="append", required=True)
    parser.add_argument("--collection")
    parser.add_argument("--expected-node-count", type=int)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        selection, rejected = select_latest_complete(
            args.manifest, args.collection, args.expected_node_count
        )
    except PreflightError as exc:
        raise SystemExit(f"Qdrant latest restore preflight failed: {exc}") from exc

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "selection": selection,
        "rejected": rejected,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    args.output_md.write_text(render_summary(selection, rejected), encoding="utf-8")
    print(selection["backup_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
