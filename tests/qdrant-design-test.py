#!/usr/bin/env python3
"""Guard the Qdrant design gate before infrastructure ownership changes."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "docs/qdrant-infrastructure-design.md"
ROADMAP = ROOT / "docs/infrastructure-platform-roadmap.md"
INVENTORY = ROOT / "docs/current-infrastructure-inventory.md"


def test_qdrant_design_exists_and_covers_required_sections() -> None:
    text = DESIGN.read_text(encoding="utf-8")

    for heading in (
        "## Current state",
        "## Recommendation",
        "## Target ownership model",
        "## Target topology",
        "## Ansible role shape",
        "## Backup and restore contract",
        "## Monitoring",
        "## Migration phases",
        "## Cutover boundaries",
    ):
        if heading not in text:
            raise AssertionError(f"Qdrant design missing required heading {heading!r}")


def test_qdrant_design_prefers_ansible_and_rejects_kamal_for_stateful_layer() -> None:
    text = DESIGN.read_text(encoding="utf-8")

    required_phrases = (
        "Use Ansible for Qdrant deployment and operations",
        "Do not use Kamal for this layer",
        "stateful infrastructure",
        "restore drills",
    )
    for phrase in required_phrases:
        if phrase not in text:
            raise AssertionError(f"Qdrant design missing recommendation phrase {phrase!r}")


def test_qdrant_design_requires_backups_restore_drill_and_two_set_retention() -> None:
    text = DESIGN.read_text(encoding="utf-8")

    for phrase in (
        "Store snapshots in S3-compatible object storage",
        "Keep the latest two completed backup sets",
        "Never delete the previous completed set until the new set has uploaded",
        "Provision a disposable `qdrant-restore-drill` environment",
        "Restore exact named snapshots",
        "Destroy drill resources after evidence upload",
    ):
        if phrase not in text:
            raise AssertionError(f"Qdrant design missing backup/restore phrase {phrase!r}")


def test_qdrant_design_keeps_first_phase_read_only() -> None:
    text = DESIGN.read_text(encoding="utf-8")

    for phrase in (
        "Add a read-only Qdrant Hetzner inventory audit workflow",
        "Add a read-only Qdrant runtime audit",
        "Do not change live servers",
        "The first Ansible PR must not",
        "restart live Qdrant containers",
        "import Terraform resources",
    ):
        if phrase not in text:
            raise AssertionError(f"Qdrant design missing safety boundary {phrase!r}")


def test_roadmap_and_inventory_link_to_design() -> None:
    roadmap = ROADMAP.read_text(encoding="utf-8")
    inventory = INVENTORY.read_text(encoding="utf-8")

    for text, name in ((roadmap, "roadmap"), (inventory, "inventory")):
        if "qdrant-infrastructure-design.md" not in text:
            raise AssertionError(f"{name} does not link to Qdrant design")


if __name__ == "__main__":
    test_qdrant_design_exists_and_covers_required_sections()
    test_qdrant_design_prefers_ansible_and_rejects_kamal_for_stateful_layer()
    test_qdrant_design_requires_backups_restore_drill_and_two_set_retention()
    test_qdrant_design_keeps_first_phase_read_only()
    test_roadmap_and_inventory_link_to_design()
