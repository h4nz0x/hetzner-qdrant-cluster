#!/usr/bin/env python3
"""Stream a Qdrant snapshot while verifying its manifest size and SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, BinaryIO


SHA256 = re.compile(r"^[A-Fa-f0-9]{64}$")


class StreamVerificationError(Exception):
    pass


def copy_and_verify(
    source: BinaryIO,
    destination: BinaryIO,
    expected_size: int,
    expected_sha256: str,
) -> dict[str, Any]:
    if expected_size <= 0:
        raise StreamVerificationError("expected size must be positive")
    if not SHA256.fullmatch(expected_sha256):
        raise StreamVerificationError("expected checksum must be a 64-character SHA-256")

    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = source.read(1024 * 1024)
        if not chunk:
            break
        destination.write(chunk)
        digest.update(chunk)
        size += len(chunk)
    destination.flush()

    actual_sha256 = digest.hexdigest()
    if size != expected_size:
        raise StreamVerificationError(
            f"snapshot stream size mismatch: expected {expected_size}, got {size}"
        )
    if actual_sha256 != expected_sha256.lower():
        raise StreamVerificationError("snapshot stream SHA-256 does not match manifest")

    return {
        "status": "verified",
        "content_length": size,
        "sha256": actual_sha256,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a streamed Qdrant snapshot")
    parser.add_argument("--expected-size", type=int, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--object", required=True)
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        result = copy_and_verify(
            sys.stdin.buffer,
            sys.stdout.buffer,
            args.expected_size,
            args.expected_sha256,
        )
    except (BrokenPipeError, StreamVerificationError) as exc:
        raise SystemExit(f"Qdrant snapshot stream verification failed: {exc}") from exc
    result["object"] = args.object
    args.report.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
