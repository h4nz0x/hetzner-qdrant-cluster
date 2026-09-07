#!/usr/bin/env python3
"""Validate Qdrant local snapshot deletion evidence for one backup run."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CleanupError(Exception):
    pass


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CleanupError(f"{path} must contain a JSON object")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise CleanupError(f"{path}:{line_number} must contain a JSON object")
        rows.append(payload)
    return rows


def build_cleanup_report(
    manifest: dict[str, Any], deletions: list[dict[str, Any]]
) -> dict[str, Any]:
    backup_id = manifest.get("backup_id")
    upload_plan = manifest.get("upload_plan")
    if not isinstance(backup_id, str) or not backup_id:
        raise CleanupError("manifest backup_id is missing")
    if not isinstance(upload_plan, list) or not upload_plan:
        raise CleanupError("manifest upload_plan is missing")

    expected: dict[tuple[str, str, str], tuple[int, str]] = {}
    for item in upload_plan:
        if not isinstance(item, dict):
            raise CleanupError("manifest upload plan contains a malformed entry")
        identity = (item.get("node"), item.get("collection"), item.get("snapshot_name"))
        if not all(isinstance(value, str) and value for value in identity):
            raise CleanupError("manifest upload plan identity is missing")
        size = item.get("size")
        checksum = item.get("checksum")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise CleanupError(f"manifest size is invalid for {identity!r}")
        if (
            not isinstance(checksum, str)
            or len(checksum) != 64
            or not all(character in "0123456789abcdefABCDEF" for character in checksum)
        ):
            raise CleanupError(f"manifest SHA-256 is invalid for {identity!r}")
        if identity in expected:
            raise CleanupError(f"duplicate manifest upload entry for {identity!r}")
        expected[identity] = (size, checksum.lower())

    actual: set[tuple[str, str, str]] = set()
    remaining_total = 0
    deleted_by_node: dict[str, int] = {}
    for row in deletions:
        identity = (row.get("node"), row.get("collection"), row.get("snapshot_name"))
        if not all(isinstance(value, str) and value for value in identity):
            raise CleanupError("deletion evidence identity is missing")
        if identity in actual:
            raise CleanupError(f"duplicate deletion evidence for {identity!r}")
        actual.add(identity)
        if identity not in expected:
            raise CleanupError(f"unexpected deletion evidence for {identity!r}")
        if row.get("deleted") is not True:
            raise CleanupError(f"Qdrant did not confirm deletion for {identity!r}")
        expected_size, expected_checksum = expected[identity]
        if row.get("validated_size") != expected_size:
            raise CleanupError(f"validated size differs from manifest for {identity!r}")
        if str(row.get("validated_sha256", "")).lower() != expected_checksum:
            raise CleanupError(f"validated SHA-256 differs from manifest for {identity!r}")
        remaining = row.get("remaining_collection_snapshots")
        if not isinstance(remaining, int) or isinstance(remaining, bool) or remaining < 0:
            raise CleanupError(f"invalid remaining snapshot count for {identity!r}")
        remaining_total += remaining
        node = identity[0]
        deleted_by_node[node] = deleted_by_node.get(node, 0) + 1

    missing = set(expected) - actual
    unexpected = actual - set(expected)
    if missing or unexpected:
        raise CleanupError(
            f"deletion evidence differs from manifest; missing={sorted(missing)!r}, "
            f"unexpected={sorted(unexpected)!r}"
        )
    if remaining_total != 0:
        raise CleanupError(
            f"{remaining_total} local collection snapshot(s) remain after cleanup"
        )

    return {
        "backup_id": backup_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "succeeded",
        "expected_snapshots": len(expected),
        "deleted_snapshots": len(actual),
        "remaining_snapshots": remaining_total,
        "nodes": [
            {"node": node, "deleted_snapshots": deleted_by_node[node]}
            for node in sorted(deleted_by_node)
        ],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Qdrant local cleanup evidence")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--deletions-jsonl", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        report = build_cleanup_report(
            read_json(args.manifest), read_jsonl(args.deletions_jsonl)
        )
    except (CleanupError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Qdrant local snapshot cleanup failed: {exc}") from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
