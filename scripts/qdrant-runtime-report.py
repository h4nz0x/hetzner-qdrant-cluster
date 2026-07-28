#!/usr/bin/env python3
"""Build a sanitized Qdrant runtime audit report from node JSON files."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_QDRANT_IMAGE = "qdrant/qdrant:v1.17.0"
EXPECTED_METRICS_PROXY_IMAGE = "nginx:1.25-alpine"
EXPECTED_NODES = ("qdrant-node-1", "qdrant-node-2", "qdrant-node-3")


@dataclass(frozen=True)
class Finding:
    severity: str
    category: str
    message: str
    details: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
            "details": self.details,
        }


def read_node_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def inspect_summary(inspect: Any) -> dict[str, Any]:
    if not isinstance(inspect, list) or not inspect:
        return {
            "present": False,
            "image": None,
            "running": False,
            "status": None,
            "restart_policy": None,
            "mount_destinations": [],
        }

    item = inspect[0]
    state = item.get("State") or {}
    host_config = item.get("HostConfig") or {}
    restart_policy = host_config.get("RestartPolicy") or {}
    mounts = item.get("Mounts") or []
    return {
        "present": True,
        "image": item.get("Config", {}).get("Image"),
        "running": bool(state.get("Running")),
        "status": state.get("Status"),
        "restart_policy": restart_policy.get("Name"),
        "mount_destinations": sorted(
            destination
            for mount in mounts
            if (destination := mount.get("Destination"))
        ),
    }


def readiness_status(payload: Any) -> str | None:
    if isinstance(payload, dict):
        status = payload.get("status")
        if status is None:
            status = payload.get("result", {}).get("status")
        return str(status).lower() if status is not None else None
    return None


def peer_count(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    peers = result.get("peers") if isinstance(result, dict) else None
    if isinstance(peers, dict):
        return len(peers)
    if isinstance(peers, list):
        return len(peers)
    return None


def storage_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {
            "path": None,
            "total_gb": None,
            "used_gb": None,
            "free_gb": None,
            "used_percent": None,
        }
    total = payload.get("total_bytes")
    used = payload.get("used_bytes")
    free = payload.get("free_bytes")
    used_percent = None
    if isinstance(total, int) and total > 0 and isinstance(used, int):
        used_percent = round((used / total) * 100, 2)

    def gb(value: Any) -> float | None:
        return round(value / (1024**3), 2) if isinstance(value, int) else None

    return {
        "path": payload.get("path"),
        "total_gb": gb(total),
        "used_gb": gb(used),
        "free_gb": gb(free),
        "used_percent": used_percent,
    }


def collection_summary(collections: Any) -> list[dict[str, Any]]:
    if not isinstance(collections, list):
        return []
    result = []
    for item in collections:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "replication_factor": item.get("replication_factor"),
                "write_consistency_factor": item.get("write_consistency_factor"),
                "snapshot_count": item.get("snapshot_count"),
                "points_count": item.get("points_count"),
            }
        )
    return sorted(result, key=lambda item: str(item.get("name")))


def sanitize_node(payload: dict[str, Any]) -> dict[str, Any]:
    qdrant = inspect_summary(payload.get("qdrant_container"))
    metrics_proxy = inspect_summary(payload.get("metrics_proxy_container"))
    return {
        "node": payload.get("node"),
        "hostname": payload.get("hostname"),
        "qdrant_container": qdrant,
        "metrics_proxy_container": metrics_proxy,
        "readiness_status": readiness_status(payload.get("readiness")),
        "cluster_peer_count": peer_count(payload.get("cluster")),
        "collections": collection_summary(payload.get("collections")),
        "storage": storage_summary(payload.get("storage")),
    }


def evaluate(nodes: list[dict[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    by_name = {node.get("node"): node for node in nodes}

    for name in EXPECTED_NODES:
        node = by_name.get(name)
        if not node:
            findings.append(
                Finding("high", "node", "Expected Qdrant runtime node is missing.", {"node": name})
            )
            continue

        qdrant = node["qdrant_container"]
        if not qdrant["present"]:
            findings.append(
                Finding("high", "container", "Qdrant container is missing.", {"node": name})
            )
        elif not qdrant["running"]:
            findings.append(
                Finding("high", "container", "Qdrant container is not running.", {"node": name})
            )
        if qdrant["image"] != EXPECTED_QDRANT_IMAGE:
            findings.append(
                Finding(
                    "medium",
                    "container",
                    "Qdrant image tag differs from the reviewed baseline.",
                    {
                        "node": name,
                        "expected_image": EXPECTED_QDRANT_IMAGE,
                        "live_image": qdrant["image"],
                    },
                )
            )

        metrics_proxy = node["metrics_proxy_container"]
        if not metrics_proxy["present"]:
            findings.append(
                Finding(
                    "medium",
                    "container",
                    "Metrics proxy container is missing.",
                    {"node": name},
                )
            )
        if metrics_proxy["image"] not in (EXPECTED_METRICS_PROXY_IMAGE, None):
            findings.append(
                Finding(
                    "medium",
                    "container",
                    "Metrics proxy image tag differs from the reviewed baseline.",
                    {
                        "node": name,
                        "expected_image": EXPECTED_METRICS_PROXY_IMAGE,
                        "live_image": metrics_proxy["image"],
                    },
                )
            )

        if node["readiness_status"] != "ok":
            findings.append(
                Finding(
                    "high",
                    "readiness",
                    "Qdrant readiness did not report ok.",
                    {"node": name, "readiness_status": node["readiness_status"]},
                )
            )

        peer_total = node["cluster_peer_count"]
        if peer_total is None or peer_total < 3:
            findings.append(
                Finding(
                    "high",
                    "cluster",
                    "Qdrant cluster peer count is below the expected three nodes.",
                    {"node": name, "cluster_peer_count": peer_total},
                )
            )

        storage = node["storage"]
        used_percent = storage.get("used_percent")
        if used_percent is None:
            findings.append(
                Finding(
                    "medium",
                    "storage",
                    "Qdrant storage usage was not captured.",
                    {"node": name},
                )
            )
        elif used_percent >= 85:
            findings.append(
                Finding(
                    "medium",
                    "storage",
                    "Qdrant storage usage is high.",
                    {"node": name, "used_percent": used_percent},
                )
            )

        for collection in node["collections"]:
            replication_factor = collection.get("replication_factor")
            snapshot_count = collection.get("snapshot_count")
            if replication_factor is None or replication_factor < 2:
                findings.append(
                    Finding(
                        "high",
                        "collection",
                        "Qdrant collection replication is below the HA baseline.",
                        {
                            "node": name,
                            "collection": collection.get("name"),
                            "replication_factor": replication_factor,
                        },
                    )
                )
            if snapshot_count is None or snapshot_count < 1:
                findings.append(
                    Finding(
                        "medium",
                        "snapshot",
                        "Qdrant collection has no visible snapshots on this node.",
                        {
                            "node": name,
                            "collection": collection.get("name"),
                            "snapshot_count": snapshot_count,
                        },
                    )
                )

    return findings


def build_report(node_files: list[Path]) -> dict[str, Any]:
    nodes = [sanitize_node(read_node_file(path)) for path in node_files]
    nodes = sorted(nodes, key=lambda item: str(item.get("node")))
    findings = evaluate(nodes)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "findings": [finding.as_dict() for finding in findings],
        "expected": {
            "qdrant_image": EXPECTED_QDRANT_IMAGE,
            "metrics_proxy_image": EXPECTED_METRICS_PROXY_IMAGE,
            "nodes": list(EXPECTED_NODES),
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Qdrant runtime audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        "- Scope: read-only SSH, local Docker inspect, local Qdrant API reads.",
        "- Mutation boundary: no container restart, no collection change, "
        "no snapshot creation/deletion.",
        "",
        "## Findings",
        "",
    ]

    if report["findings"]:
        for finding in report["findings"]:
            lines.append(
                f"- **{finding['severity']}** `{finding['category']}`: {finding['message']}"
            )
            if finding["details"]:
                lines.append(f"  - Details: `{json.dumps(finding['details'], sort_keys=True)}`")
    else:
        lines.append("- No findings.")

    lines.extend(["", "## Nodes", ""])
    for node in report["nodes"]:
        qdrant = node["qdrant_container"]
        metrics = node["metrics_proxy_container"]
        storage = node["storage"]
        lines.append(
            f"- `{node['node']}`: qdrant `{qdrant['image']}` "
            f"running={qdrant['running']}, metrics `{metrics['image']}` "
            f"running={metrics['running']}, readiness `{node['readiness_status']}`, "
            f"peers `{node['cluster_peer_count']}`, storage used "
            f"`{storage['used_percent']}`%"
        )

    lines.extend(["", "## Collections", ""])
    seen: set[tuple[str, str]] = set()
    for node in report["nodes"]:
        for collection in node["collections"]:
            key = (str(node["node"]), str(collection["name"]))
            if key in seen:
                continue
            seen.add(key)
            lines.append(
                f"- `{node['node']}` / `{collection['name']}`: status "
                f"`{collection['status']}`, replication "
                f"`{collection['replication_factor']}`, snapshots "
                f"`{collection['snapshot_count']}`"
            )
    if not seen:
        lines.append("- No collections reported.")

    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build sanitized Qdrant runtime audit report")
    parser.add_argument("--node-json", type=Path, action="append", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    report = build_report(args.node_json)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    args.output_md.write_text(render_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
