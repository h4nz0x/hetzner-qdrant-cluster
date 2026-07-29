#!/usr/bin/env python3
"""Select old Qdrant S3 backup prefixes for deletion."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


BACKUP_PREFIX = re.compile(
    r"^(?P<root>.*?)(?P<backup_id>[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z)/$"
)


class RetentionError(Exception):
    pass


def common_prefixes(payload: dict[str, Any]) -> list[str]:
    prefixes = []
    for item in payload.get("CommonPrefixes", []) or []:
        prefix = item.get("Prefix")
        if isinstance(prefix, str):
            prefixes.append(prefix)
    return prefixes


def select_delete_prefixes(
    payload: dict[str, Any],
    keep: int,
    protected_backup_id: str,
) -> list[str]:
    if keep < 1:
        raise RetentionError("keep must be at least 1")
    backups: list[tuple[str, str]] = []
    for prefix in common_prefixes(payload):
        match = BACKUP_PREFIX.fullmatch(prefix)
        if not match:
            continue
        backups.append((match.group("backup_id"), prefix))

    backups.sort(reverse=True)
    retained = {prefix for _, prefix in backups[:keep]}
    delete = [prefix for backup_id, prefix in backups[keep:] if backup_id != protected_backup_id]

    protected_prefixes = [
        prefix for backup_id, prefix in backups if backup_id == protected_backup_id
    ]
    if not protected_prefixes:
        raise RetentionError("protected backup id is not present in listed prefixes")
    if protected_prefixes[0] not in retained:
        raise RetentionError("protected backup id would not be retained; refusing deletion")
    return delete


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan Qdrant S3 backup retention")
    parser.add_argument("--prefixes-json", type=Path, required=True)
    parser.add_argument("--keep", type=int, default=2)
    parser.add_argument("--protected-backup-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    payload = json.loads(args.prefixes_json.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("prefixes JSON must be an object")
    try:
        delete = select_delete_prefixes(payload, args.keep, args.protected_backup_id)
    except RetentionError as exc:
        raise SystemExit(f"Qdrant S3 retention planning failed: {exc}") from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(f"{prefix}\n" for prefix in delete), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
