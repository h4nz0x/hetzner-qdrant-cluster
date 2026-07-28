#!/usr/bin/env python3
"""Read-only Qdrant Hetzner inventory audit.

This script compares the expected live Qdrant estate with sanitized Hetzner
Cloud metadata. It does not SSH to hosts, call Docker, mutate Terraform state,
or write to Qdrant.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HCLOUD_API = "https://api.hetzner.cloud/v1"

EXPECTED_SERVERS = {
    "qdrant-node-1": {"private_ip": "10.2.0.2", "server_type": "ccx43"},
    "qdrant-node-2": {"private_ip": "10.2.0.3", "server_type": "ccx43"},
    "qdrant-node-3": {"private_ip": "10.2.0.5", "server_type": "ccx33"},
    "qdrant-nginx-proxy": {"private_ip": "10.2.0.6", "server_type": "cpx11"},
}

EXPECTED_VOLUMES = {
    "qdrant-vol-1": {"size_gb": 512, "server": "qdrant-node-1"},
    "qdrant-vol-2": {"size_gb": 512, "server": "qdrant-node-2"},
    "qdrant-vol-3": {"size_gb": 512, "server": "qdrant-node-3"},
}

EXPECTED_LOAD_BALANCERS = {
    "qdrant-lb": {"private_ip": "10.2.0.4"},
    "qdrant-grpc-lb": {"private_ip": "10.2.0.7"},
    "qdrant-metrics-lb": {"private_ip": "10.2.0.8"},
}


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


def hcloud_get_collection(token: str, collection: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        url = f"{HCLOUD_API}/{collection}?per_page=50&page={page}"
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "example-qdrant-live-audit",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise SystemExit(
                f"Hetzner API request failed for {collection}: HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise SystemExit(f"Hetzner API request failed for {collection}: {exc.reason}") from exc

        items.extend(payload.get(collection, []))
        pagination = payload.get("meta", {}).get("pagination", {})
        if not pagination.get("next_page"):
            return items
        page = int(pagination["next_page"])


def fetch_hcloud_inventory(token: str) -> dict[str, Any]:
    return {
        "servers": hcloud_get_collection(token, "servers"),
        "volumes": hcloud_get_collection(token, "volumes"),
        "firewalls": hcloud_get_collection(token, "firewalls"),
        "networks": hcloud_get_collection(token, "networks"),
        "load_balancers": hcloud_get_collection(token, "load_balancers"),
    }


def private_ips(resource: dict[str, Any]) -> list[str]:
    ips: list[str] = []
    for attachment in resource.get("private_net", []) or []:
        ip = attachment.get("ip")
        if ip:
            ips.append(str(ip))
    return sorted(ips)


def attached_server_name(volume: dict[str, Any]) -> str | None:
    server = volume.get("server")
    if isinstance(server, dict):
        name = server.get("name")
        return str(name) if name else None
    return None


def compact_inventory(raw: dict[str, Any]) -> dict[str, Any]:
    server_names = set(EXPECTED_SERVERS)
    volume_names = set(EXPECTED_VOLUMES)
    load_balancer_names = set(EXPECTED_LOAD_BALANCERS)

    servers = [
        {
            "id": server.get("id"),
            "name": server.get("name"),
            "server_type": (server.get("server_type") or {}).get("name"),
            "location": ((server.get("datacenter") or {}).get("location") or {}).get("name"),
            "private_ips": private_ips(server),
            "labels": server.get("labels") or {},
        }
        for server in raw.get("servers", [])
        if server.get("name") in server_names
    ]

    volumes = [
        {
            "id": volume.get("id"),
            "name": volume.get("name"),
            "size_gb": volume.get("size"),
            "location": (volume.get("location") or {}).get("name"),
            "server": attached_server_name(volume),
        }
        for volume in raw.get("volumes", [])
        if volume.get("name") in volume_names
    ]

    load_balancers = [
        {
            "id": lb.get("id"),
            "name": lb.get("name"),
            "type": (lb.get("load_balancer_type") or {}).get("name"),
            "location": (lb.get("location") or {}).get("name"),
            "private_ips": private_ips(lb),
            "service_ports": sorted(
                {
                    service.get("listen_port")
                    for service in lb.get("services", []) or []
                    if service.get("listen_port") is not None
                }
            ),
        }
        for lb in raw.get("load_balancers", [])
        if lb.get("name") in load_balancer_names
    ]

    qdrant_networks = []
    for network in raw.get("networks", []):
        if network.get("name") == "qdrant" or network.get("ip_range") == "10.2.0.0/16":
            qdrant_networks.append(
                {
                    "id": network.get("id"),
                    "name": network.get("name"),
                    "ip_range": network.get("ip_range"),
                }
            )

    firewalls = [
        {"id": firewall.get("id"), "name": firewall.get("name")}
        for firewall in raw.get("firewalls", [])
        if "qdrant" in str(firewall.get("name", "")).lower()
    ]

    return {
        "servers": sorted(servers, key=lambda item: item["name"]),
        "volumes": sorted(volumes, key=lambda item: item["name"]),
        "load_balancers": sorted(load_balancers, key=lambda item: item["name"]),
        "networks": sorted(qdrant_networks, key=lambda item: str(item["name"])),
        "firewalls": sorted(firewalls, key=lambda item: str(item["name"])),
    }


def evaluate(compact: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []

    servers = {item["name"]: item for item in compact["servers"]}
    for name, expected in EXPECTED_SERVERS.items():
        server = servers.get(name)
        if not server:
            findings.append(
                Finding(
                    "high",
                    "server",
                    "Expected Qdrant server is missing live.",
                    {"server": name},
                )
            )
            continue
        if expected["private_ip"] not in server["private_ips"]:
            findings.append(
                Finding(
                    "high",
                    "server",
                    "Expected Qdrant server private IP is missing live.",
                    {
                        "server": name,
                        "expected_private_ip": expected["private_ip"],
                        "live_private_ips": server["private_ips"],
                    },
                )
            )
        if server.get("server_type") != expected["server_type"]:
            findings.append(
                Finding(
                    "medium",
                    "server",
                    "Qdrant server type differs from inventory.",
                    {
                        "server": name,
                        "expected_server_type": expected["server_type"],
                        "live_server_type": server.get("server_type"),
                    },
                )
            )

    volumes = {item["name"]: item for item in compact["volumes"]}
    for name, expected in EXPECTED_VOLUMES.items():
        volume = volumes.get(name)
        if not volume:
            findings.append(
                Finding(
                    "high",
                    "volume",
                    "Expected Qdrant volume is missing live.",
                    {"volume": name},
                )
            )
            continue
        if volume.get("size_gb") != expected["size_gb"]:
            findings.append(
                Finding(
                    "high",
                    "volume",
                    "Qdrant volume size differs from inventory.",
                    {
                        "volume": name,
                        "expected_size_gb": expected["size_gb"],
                        "live_size_gb": volume.get("size_gb"),
                    },
                )
            )
        if volume.get("server") != expected["server"]:
            findings.append(
                Finding(
                    "high",
                    "volume",
                    "Qdrant volume is attached to the wrong server.",
                    {
                        "volume": name,
                        "expected_server": expected["server"],
                        "live_server": volume.get("server"),
                    },
                )
            )

    load_balancers = {item["name"]: item for item in compact["load_balancers"]}
    for name, expected in EXPECTED_LOAD_BALANCERS.items():
        lb = load_balancers.get(name)
        if not lb:
            findings.append(
                Finding(
                    "high",
                    "load_balancer",
                    "Expected Qdrant load balancer is missing live.",
                    {"load_balancer": name},
                )
            )
            continue
        if expected["private_ip"] not in lb["private_ips"]:
            findings.append(
                Finding(
                    "high",
                    "load_balancer",
                    "Expected Qdrant load balancer private IP is missing live.",
                    {
                        "load_balancer": name,
                        "expected_private_ip": expected["private_ip"],
                        "live_private_ips": lb["private_ips"],
                    },
                )
            )

    if not compact["networks"]:
        findings.append(
            Finding(
                "medium",
                "network",
                "Qdrant private network was not identified in live metadata.",
                {"expected_ip_range": "10.2.0.0/16"},
            )
        )

    if not compact["firewalls"]:
        findings.append(
            Finding(
                "medium",
                "firewall",
                "No Qdrant-named firewall was identified in live metadata.",
                {},
            )
        )

    return findings


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Qdrant live inventory audit",
        "",
        f"- Generated: `{report['generated_at']}`",
        "- Scope: Hetzner Cloud metadata only; no SSH, Docker, Qdrant API, or Terraform mutation.",
        "",
        "## Resource Counts",
        "",
        f"- Servers: {len(report['inventory']['servers'])}",
        f"- Volumes: {len(report['inventory']['volumes'])}",
        f"- Load balancers: {len(report['inventory']['load_balancers'])}",
        f"- Qdrant networks: {len(report['inventory']['networks'])}",
        f"- Qdrant firewalls: {len(report['inventory']['firewalls'])}",
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

    lines.extend(["", "## Expected Recovery-Relevant Assets", ""])
    for server in report["inventory"]["servers"]:
        lines.append(
            f"- `{server['name']}`: type `{server['server_type']}`, "
            f"private IPs `{', '.join(server['private_ips']) or '-'}`"
        )
    for volume in report["inventory"]["volumes"]:
        lines.append(
            f"- `{volume['name']}`: {volume['size_gb']} GiB, attached to `{volume['server']}`"
        )
    for lb in report["inventory"]["load_balancers"]:
        lines.append(
            f"- `{lb['name']}`: private IPs `{', '.join(lb['private_ips']) or '-'}`, "
            f"ports `{', '.join(str(port) for port in lb['service_ports']) or '-'}`"
        )
    lines.append("")
    return "\n".join(lines)


def build_report(raw: dict[str, Any]) -> dict[str, Any]:
    compact = compact_inventory(raw)
    findings = evaluate(compact)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inventory": compact,
        "findings": [finding.as_dict() for finding in findings],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Qdrant inventory audit")
    parser.add_argument("--hcloud-json", type=Path, help="Read fixture JSON instead of Hetzner API")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.hcloud_json:
        raw = json.loads(args.hcloud_json.read_text(encoding="utf-8"))
    else:
        token = os.getenv("HCLOUD_TOKEN")
        if not token:
            raise SystemExit("HCLOUD_TOKEN is required unless --hcloud-json is provided")
        raw = fetch_hcloud_inventory(token)

    report = build_report(raw)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    args.output_md.write_text(render_markdown(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
