#!/usr/bin/env python3
"""Export Qdrant backup manifest health as node_exporter textfile metrics."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path("/var/lib/qdrant-backup/latest/qdrant-backup-manifest.json")
DEFAULT_OUTPUT = Path("/var/lib/node_exporter/textfile/qdrant_backup.prom")


def escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def labels(cluster: str, environment: str, extra: dict[str, str] | None = None) -> str:
    values = {"cluster": cluster, "environment": environment}
    values.update(extra or {})
    return "{" + ",".join(f'{key}="{escape_label(value)}"' for key, value in values.items()) + "}"


def parse_timestamp(value: str) -> int:
    normalized = value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest is not a JSON object")
    return payload


def metric_header(name: str, help_text: str, metric_type: str = "gauge") -> list[str]:
    return [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} {metric_type}",
    ]


def failure_metrics(cluster: str, environment: str, reason: str, retention_keep: int) -> list[str]:
    base = labels(cluster, environment)
    reason_labels = labels(cluster, environment, {"reason": reason})
    now = int(time.time())
    lines: list[str] = []
    lines += metric_header("qdrant_backup_manifest_present", "1 if the latest Qdrant backup manifest exists and is parseable.")
    lines.append(f"qdrant_backup_manifest_present{reason_labels} 0")
    lines += metric_header("qdrant_backup_last_success", "1 if the latest Qdrant backup manifest represents a valid completed backup.")
    lines.append(f"qdrant_backup_last_success{reason_labels} 0")
    lines += metric_header("qdrant_backup_retention_keep", "Configured number of completed Qdrant backup prefixes retained in S3.")
    lines.append(f"qdrant_backup_retention_keep{base} {retention_keep}")
    lines += metric_header("qdrant_backup_metric_export_timestamp_seconds", "Unix timestamp of the latest Qdrant backup metric export attempt.")
    lines.append(f"qdrant_backup_metric_export_timestamp_seconds{base} {now}")
    return lines


def success_metrics(
    manifest: dict[str, Any],
    cluster: str,
    environment: str,
    retention_keep: int,
) -> list[str]:
    backup_id = manifest.get("backup_id")
    generated_at = manifest.get("generated_at")
    nodes = manifest.get("nodes")
    upload_plan = manifest.get("upload_plan")
    if not isinstance(backup_id, str):
        raise ValueError("backup_id is missing")
    if not isinstance(generated_at, str):
        raise ValueError("generated_at is missing")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("nodes list is missing")
    if not isinstance(upload_plan, list) or not upload_plan:
        raise ValueError("upload_plan list is missing")

    total_bytes = 0
    collections: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("node entry is malformed")
        node_collections = node.get("collections")
        if not isinstance(node_collections, list) or not node_collections:
            raise ValueError("node collections are missing")
        for item in node_collections:
            if not isinstance(item, dict):
                raise ValueError("collection entry is malformed")
            collection = item.get("collection")
            size = item.get("size")
            if not isinstance(collection, str):
                raise ValueError("collection name is missing")
            if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                raise ValueError("snapshot size is invalid")
            collections.add(collection)
            total_bytes += size

    base = labels(cluster, environment)
    ok = labels(cluster, environment, {"reason": "ok"})
    now = int(time.time())
    backup_ts = parse_timestamp(backup_id)
    generated_ts = parse_timestamp(generated_at)

    lines: list[str] = []
    lines += metric_header("qdrant_backup_manifest_present", "1 if the latest Qdrant backup manifest exists and is parseable.")
    lines.append(f"qdrant_backup_manifest_present{ok} 1")
    lines += metric_header("qdrant_backup_last_success", "1 if the latest Qdrant backup manifest represents a valid completed backup.")
    lines.append(f"qdrant_backup_last_success{ok} 1")
    lines += metric_header("qdrant_backup_last_success_timestamp_seconds", "Unix timestamp of the latest completed Qdrant backup ID.")
    lines.append(f"qdrant_backup_last_success_timestamp_seconds{base} {backup_ts}")
    lines += metric_header("qdrant_backup_manifest_generated_timestamp_seconds", "Unix timestamp when the latest Qdrant backup manifest was generated.")
    lines.append(f"qdrant_backup_manifest_generated_timestamp_seconds{base} {generated_ts}")
    lines += metric_header("qdrant_backup_nodes", "Number of Qdrant nodes represented by the latest backup manifest.")
    lines.append(f"qdrant_backup_nodes{base} {len(nodes)}")
    lines += metric_header("qdrant_backup_snapshot_objects", "Number of Qdrant snapshot objects in the latest backup upload plan.")
    lines.append(f"qdrant_backup_snapshot_objects{base} {len(upload_plan)}")
    lines += metric_header("qdrant_backup_collections", "Number of unique Qdrant collections represented by the latest backup manifest.")
    lines.append(f"qdrant_backup_collections{base} {len(collections)}")
    lines += metric_header("qdrant_backup_total_bytes", "Total bytes across all Qdrant collection snapshots in the latest backup manifest.")
    lines.append(f"qdrant_backup_total_bytes{base} {total_bytes}")
    lines += metric_header("qdrant_backup_retention_keep", "Configured number of completed Qdrant backup prefixes retained in S3.")
    lines.append(f"qdrant_backup_retention_keep{base} {retention_keep}")
    lines += metric_header("qdrant_backup_metric_export_timestamp_seconds", "Unix timestamp of the latest Qdrant backup metric export attempt.")
    lines.append(f"qdrant_backup_metric_export_timestamp_seconds{base} {now}")
    return lines


def write_textfile(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
            handle.write("\n")
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Qdrant backup metrics")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--cluster", default="qdrant")
    parser.add_argument("--environment", default="production")
    parser.add_argument("--retention-keep", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = read_manifest(args.manifest)
        lines = success_metrics(
            manifest=manifest,
            cluster=args.cluster,
            environment=args.environment,
            retention_keep=args.retention_keep,
        )
    except Exception as exc:
        lines = failure_metrics(
            cluster=args.cluster,
            environment=args.environment,
            reason=exc.__class__.__name__,
            retention_keep=args.retention_keep,
        )
    write_textfile(args.output, lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
