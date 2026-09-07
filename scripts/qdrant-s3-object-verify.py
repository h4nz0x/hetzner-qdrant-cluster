#!/usr/bin/env python3
"""Verify sanitized S3 object metadata before local Qdrant cleanup."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any


SHA256 = re.compile(r"^[A-Fa-f0-9]{64}$")
ALLOWED_ENCRYPTION = {"AES256", "aws:kms", "aws:kms:dsse"}


class VerificationError(Exception):
    pass


def verify_head_object(
    payload: dict[str, Any],
    expected_size: int,
    expected_sha256: str,
    strict: bool = True,
) -> dict[str, Any]:
    """Validate a head-object response.

    strict=True (AWS S3) additionally requires the S3-computed SHA-256 checksum
    and server-side encryption. S3-compatible stores such as Hetzner Object
    Storage do not return those fields, so strict=False only checks size, ETag
    and the SHA-256 recorded as object metadata by the uploader.
    """
    if expected_size <= 0:
        raise VerificationError("expected size must be positive")
    if not SHA256.fullmatch(expected_sha256):
        raise VerificationError("expected checksum must be a 64-character SHA-256")

    content_length = payload.get("ContentLength")
    if content_length != expected_size:
        raise VerificationError(
            f"S3 object size mismatch: expected {expected_size}, got {content_length!r}"
        )

    metadata = payload.get("Metadata")
    if not isinstance(metadata, dict):
        raise VerificationError("S3 object checksum metadata is missing")
    normalized_metadata = {str(key).lower(): str(value) for key, value in metadata.items()}
    recorded_sha256 = normalized_metadata.get("qdrant-sha256", "").lower()
    if recorded_sha256 != expected_sha256.lower():
        raise VerificationError("S3 object SHA-256 metadata does not match the manifest")

    checksum_sha256 = payload.get("ChecksumSHA256")
    if strict and (not isinstance(checksum_sha256, str) or not checksum_sha256):
        raise VerificationError("S3 did not return its stored SHA-256 checksum")

    etag = payload.get("ETag")
    if not isinstance(etag, str) or not etag.strip('"'):
        raise VerificationError("S3 object ETag is missing")

    encryption = payload.get("ServerSideEncryption")
    if strict and encryption not in ALLOWED_ENCRYPTION:
        raise VerificationError(
            f"S3 object encryption is missing or unsupported: {encryption!r}"
        )

    return {
        "status": "verified",
        "content_length": content_length,
        "expected_sha256": expected_sha256.lower(),
        "s3_checksum_sha256": checksum_sha256,
        "etag": etag.strip('"'),
        "server_side_encryption": encryption,
        "strict": strict,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify an S3 Qdrant backup object")
    parser.add_argument("--expected-size", type=int, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--object", required=True)
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Skip S3 checksum and encryption checks (S3-compatible storage)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise SystemExit("S3 head-object response must be a JSON object")
    try:
        result = verify_head_object(
            payload, args.expected_size, args.expected_sha256, strict=not args.no_strict
        )
    except VerificationError as exc:
        raise SystemExit(f"S3 object verification failed for {args.object}: {exc}") from exc
    result["object"] = args.object
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
