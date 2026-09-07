#!/usr/bin/env python3
"""Plan and verify a disposable Qdrant snapshot restore drill."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")
BACKUP_ID = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class RestoreDrillError(Exception):
    pass


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RestoreDrillError(f"{path} must contain a JSON object")
    return payload


def require_safe(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_NAME.fullmatch(value):
        raise RestoreDrillError(f"{label} must match {SAFE_NAME.pattern}; got {value!r}")
    return value


def node_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, list):
        raise RestoreDrillError("manifest nodes must be a list")
    result = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise RestoreDrillError("manifest contains a malformed node")
        name = require_safe(node.get("node"), "node")
        result[name] = node
    if not result:
        raise RestoreDrillError("manifest has no nodes")
    return result


def select_snapshot(
    manifest: dict[str, Any],
    backup_id: str,
    source_node: str,
    collection: str | None,
    snapshot_root: Path,
) -> dict[str, Any]:
    if not BACKUP_ID.fullmatch(backup_id):
        raise RestoreDrillError("backup_id must be an exact UTC timestamp")
    if manifest.get("backup_id") != backup_id:
        raise RestoreDrillError("manifest backup_id does not match requested backup_id")

    source_node = require_safe(source_node, "source_node")
    by_node = node_map(manifest)
    if source_node not in by_node:
        raise RestoreDrillError(f"source node {source_node} is not present in manifest")

    collections = by_node[source_node].get("collections")
    if not isinstance(collections, list) or not collections:
        raise RestoreDrillError(f"{source_node} has no collection snapshots")

    if collection is None:
        selected = sorted(collections, key=lambda item: str(item.get("collection")))[0]
    else:
        collection = require_safe(collection, "collection")
        selected = next(
            (item for item in collections if item.get("collection") == collection),
            None,
        )
        if selected is None:
            raise RestoreDrillError(
                f"{source_node} has no snapshot for collection {collection}"
            )

    if not isinstance(selected, dict):
        raise RestoreDrillError("selected snapshot is malformed")
    collection_name = require_safe(selected.get("collection"), "collection")
    snapshot_name = require_safe(selected.get("snapshot_name"), "snapshot_name")
    size = selected.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise RestoreDrillError("selected snapshot has invalid size")

    prefix = str(manifest.get("prefix") or "").strip("/")
    bucket = str(manifest.get("bucket") or "")
    if not prefix or not bucket:
        raise RestoreDrillError("manifest bucket and prefix are required")

    s3_key = (
        f"{prefix}/{backup_id}/nodes/{source_node}/collections/"
        f"{collection_name}/{snapshot_name}"
    )
    local_snapshot = snapshot_root / collection_name / snapshot_name
    file_uri = (
        "file:///qdrant/snapshots/"
        f"{urllib.parse.quote(collection_name)}/"
        f"{urllib.parse.quote(snapshot_name)}"
    )
    return {
        "backup_id": backup_id,
        "bucket": bucket,
        "prefix": prefix,
        "source_node": source_node,
        "collection": collection_name,
        "snapshot_name": snapshot_name,
        "expected_size": size,
        "checksum": selected.get("checksum"),
        "creation_time": selected.get("creation_time"),
        "s3_key": s3_key,
        "local_snapshot_path": str(local_snapshot),
        "qdrant_file_uri": file_uri,
    }


def collection_names(node: dict[str, Any]) -> set[str]:
    collections = node.get("collections")
    if not isinstance(collections, list):
        raise RestoreDrillError("manifest node collections must be a list")
    names = set()
    for item in collections:
        if not isinstance(item, dict):
            raise RestoreDrillError("manifest contains a malformed collection")
        name = item.get("collection")
        if isinstance(name, str):
            names.add(name)
    return names


def select_snapshot_set(
    manifest: dict[str, Any],
    backup_id: str,
    collection: str | None,
    snapshot_root: Path,
) -> dict[str, Any]:
    if not BACKUP_ID.fullmatch(backup_id):
        raise RestoreDrillError("backup_id must be an exact UTC timestamp")
    if manifest.get("backup_id") != backup_id:
        raise RestoreDrillError("manifest backup_id does not match requested backup_id")

    by_node = node_map(manifest)
    expected_nodes = list(by_node)
    if collection is None:
        common = set.intersection(*(collection_names(by_node[node]) for node in expected_nodes))
        if not common:
            raise RestoreDrillError("manifest has no collection snapshot present on every node")
        collection_name = sorted(common)[0]
    else:
        collection_name = require_safe(collection, "collection")

    prefix = str(manifest.get("prefix") or "").strip("/")
    bucket = str(manifest.get("bucket") or "")
    if not prefix or not bucket:
        raise RestoreDrillError("manifest bucket and prefix are required")

    snapshots: list[dict[str, Any]] = []
    for node_name in expected_nodes:
        collections = by_node[node_name].get("collections")
        if not isinstance(collections, list):
            raise RestoreDrillError(f"{node_name} collections must be a list")
        selected = next(
            (item for item in collections if item.get("collection") == collection_name),
            None,
        )
        if selected is None:
            raise RestoreDrillError(
                f"{node_name} has no snapshot for collection {collection_name}"
            )
        if not isinstance(selected, dict):
            raise RestoreDrillError(f"{node_name} selected snapshot is malformed")
        snapshot_name = require_safe(selected.get("snapshot_name"), "snapshot_name")
        size = selected.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise RestoreDrillError(f"{node_name} selected snapshot has invalid size")
        s3_key = (
            f"{prefix}/{backup_id}/nodes/{node_name}/collections/"
            f"{collection_name}/{snapshot_name}"
        )
        snapshots.append(
            {
                "node": node_name,
                "collection": collection_name,
                "snapshot_name": snapshot_name,
                "expected_size": size,
                "checksum": selected.get("checksum"),
                "creation_time": selected.get("creation_time"),
                "s3_key": s3_key,
                "local_snapshot_path": str(snapshot_root / node_name / snapshot_name),
            }
        )

    return {
        "backup_id": backup_id,
        "bucket": bucket,
        "prefix": prefix,
        "collection": collection_name,
        "nodes": expected_nodes,
        "snapshots": snapshots,
        "total_expected_size": sum(item["expected_size"] for item in snapshots),
    }


def url_json(
    method: str,
    url: str,
    body: bytes | None = None,
    timeout: int = 30,
) -> dict[str, Any] | list[Any] | None:
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    if not data:
        return None
    payload = json.loads(data.decode("utf-8"))
    if isinstance(payload, (dict, list)):
        return payload
    return None


def collection_status(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    status = result.get("status")
    return str(status).lower() if status is not None else None


def collection_points(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    points = result.get("points_count")
    return points if isinstance(points, int) and not isinstance(points, bool) else None


def scroll_count(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    result = payload.get("result")
    if not isinstance(result, dict):
        return 0
    points = result.get("points")
    return len(points) if isinstance(points, list) else 0


def verify_restore(qdrant_url: str, collection: str) -> dict[str, Any]:
    collection = require_safe(collection, "collection")
    base_url = qdrant_url.rstrip("/")
    encoded = urllib.parse.quote(collection, safe="")
    collections = url_json("GET", f"{base_url}/collections")
    collection_info = url_json("GET", f"{base_url}/collections/{encoded}")
    scroll_body = json.dumps(
        {"limit": 1, "with_payload": False, "with_vector": False}
    ).encode("utf-8")
    scroll = url_json("POST", f"{base_url}/collections/{encoded}/points/scroll", scroll_body)

    api_status = None
    if isinstance(collections, dict):
        status = collections.get("status")
        api_status = str(status).lower() if status is not None else None

    status = collection_status(collection_info)
    points = collection_points(collection_info)
    sample_points = scroll_count(scroll)
    passed = (
        api_status == "ok"
        and status in {"green", "yellow"}
        and points is not None
        and points > 0
        and sample_points > 0
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "collection": collection,
        "api_status": api_status,
        "collection_status": status,
        "points_count": points,
        "sample_points_returned": sample_points,
        "passed": passed,
    }


def render_plan_summary(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Qdrant restore drill plan",
            "",
            f"- Backup ID: `{plan['backup_id']}`",
            f"- Source node: `{plan['source_node']}`",
            f"- Collection: `{plan['collection']}`",
            f"- Snapshot: `{plan['snapshot_name']}`",
            f"- S3 object: `s3://{plan['bucket']}/{plan['s3_key']}`",
            f"- Expected size: {plan['expected_size']} bytes",
            "",
        ]
    )


def render_snapshot_set_summary(plan: dict[str, Any]) -> str:
    lines = [
        "# Qdrant distributed restore drill plan",
        "",
        f"- Backup ID: `{plan['backup_id']}`",
        f"- Collection: `{plan['collection']}`",
        f"- Nodes: {len(plan['snapshots'])}",
        f"- Total expected size: {plan['total_expected_size']} bytes",
        "",
        "| Node | Snapshot | Expected size | S3 object |",
        "| --- | --- | ---: | --- |",
    ]
    for item in plan["snapshots"]:
        lines.append(
            "| "
            f"`{item['node']}` | "
            f"`{item['snapshot_name']}` | "
            f"{item['expected_size']} | "
            f"`s3://{plan['bucket']}/{item['s3_key']}` |"
        )
    lines.append("")
    return "\n".join(lines)


def render_verify_summary(report: dict[str, Any]) -> str:
    verdict = "passed" if report["passed"] else "failed"
    return "\n".join(
        [
            "# Qdrant restore drill result",
            "",
            f"- Result: `{verdict}`",
            f"- Collection: `{report['collection']}`",
            f"- API status: `{report['api_status']}`",
            f"- Collection status: `{report['collection_status']}`",
            f"- Points count: `{report['points_count']}`",
            f"- Sample points returned: `{report['sample_points_returned']}`",
            "",
        ]
    )


def plan_command(args: argparse.Namespace) -> int:
    manifest = read_json(args.manifest)
    plan = select_snapshot_set(
        manifest,
        args.backup_id,
        args.collection,
        args.snapshot_dir,
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    args.output_md.write_text(render_snapshot_set_summary(plan), encoding="utf-8")
    return 0


def verify_command(args: argparse.Namespace) -> int:
    report = verify_restore(args.qdrant_url, args.collection)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    args.output_md.write_text(render_verify_summary(report), encoding="utf-8")
    if not report["passed"]:
        raise SystemExit("Qdrant restore drill verification failed")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Qdrant restore drill helpers")
    subcommands = parser.add_subparsers(dest="command", required=True)

    plan = subcommands.add_parser("plan", help="Build a restore drill plan")
    plan.add_argument("--manifest", type=Path, required=True)
    plan.add_argument("--backup-id", required=True)
    plan.add_argument("--collection")
    plan.add_argument("--snapshot-dir", type=Path, required=True)
    plan.add_argument("--output-json", type=Path, required=True)
    plan.add_argument("--output-md", type=Path, required=True)
    plan.set_defaults(func=plan_command)

    verify = subcommands.add_parser("verify", help="Verify a restored collection")
    verify.add_argument("--qdrant-url", required=True)
    verify.add_argument("--collection", required=True)
    verify.add_argument("--output-json", type=Path, required=True)
    verify.add_argument("--output-md", type=Path, required=True)
    verify.set_defaults(func=verify_command)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        return args.func(args)
    except RestoreDrillError as exc:
        raise SystemExit(f"Qdrant restore drill failed: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
