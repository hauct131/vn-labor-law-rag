#!/usr/bin/env python3
"""Validate the technical completeness and hashes of a Step-2 source registry."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


RISK_DOCUMENTS = {
    "135/2020/NĐ-CP",
    "145/2020/NĐ-CP",
    "152/2020/NĐ-CP",
    "128/2025/NĐ-CP",
    "129/2025/NĐ-CP",
    "09/2020/TT-BLĐTBXH",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate(root: Path, registry: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    documents = registry.get("documents", [])
    numbers = [item.get("document_number") for item in documents]
    kinds = Counter(item.get("source_kind") for item in documents)

    if registry.get("registry_status") != "draft_pending_legal_review":
        errors.append("registry_status must be draft_pending_legal_review")
    if len(documents) != 18:
        errors.append(f"document_count: expected 18, got {len(documents)}")
    if len(set(numbers)) != len(numbers):
        errors.append("duplicate document_number")
    if kinds["vbpl_snapshot"] != 16:
        errors.append(f"vbpl_snapshot_count: expected 16, got {kinds['vbpl_snapshot']}")
    if kinds["user_supplied_official_docx"] != 2:
        errors.append(
            "official_docx_count: expected 2, "
            f"got {kinds['user_supplied_official_docx']}"
        )

    unit_count = sum(
        item.get("corpus_scope", {}).get("included_unit_count", 0) for item in documents
    )
    if unit_count != 513:
        errors.append(f"included_unit_count: expected 513, got {unit_count}")

    integrity_failures: list[str] = []
    for item in documents:
        number = item.get("document_number", "<unknown>")
        if not item.get("official_identity_url") and not item.get("acquisition", {}).get("detail_url"):
            errors.append(f"{number}: no official identity/acquisition URL")
        if item.get("legal_review", {}).get("authority_review_passed") is not False:
            errors.append(f"{number}: authority_review_passed must remain false in draft")
        if item.get("source_kind") == "vbpl_snapshot":
            path_value = item.get("acquisition", {}).get("primary_file_path")
            declared = item.get("integrity", {}).get("content_sha256")
        else:
            path_value = item.get("acquisition", {}).get("source_file_path")
            declared = item.get("integrity", {}).get("source_file_sha256")
        path = root / path_value if path_value else None
        if path is None or not path.is_file():
            integrity_failures.append(f"{number}: missing primary source file {path_value}")
        elif sha256(path) != declared:
            integrity_failures.append(f"{number}: primary source hash mismatch")

    by_number = {item.get("document_number"): item for item in documents}
    for number in sorted(RISK_DOCUMENTS):
        review = by_number.get(number, {}).get("legal_review", {})
        if review.get("status") != "pending" or review.get("decision") != "unresolved":
            errors.append(f"{number}: high-risk document must remain pending/unresolved")

    errors.extend(integrity_failures)
    pending = sum(item.get("legal_review", {}).get("status") == "pending" for item in documents)
    return {
        "schema_version": "source-registry-validation-v1",
        "document_count": len(documents),
        "vbpl_snapshot_count": kinds["vbpl_snapshot"],
        "official_docx_count": kinds["user_supplied_official_docx"],
        "included_unit_count": unit_count,
        "integrity_failures": len(integrity_failures),
        "legal_review_pending": pending,
        "authority_review_pending": sum(
            item.get("legal_review", {}).get("authority_review_passed") is False
            for item in documents
        ),
        "registry_status": registry.get("registry_status"),
        "passed": not errors,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--registry", default="data/governance/source_registry.draft.json")
    parser.add_argument("--output", default="data/governance/source_registry_validation.json")
    args = parser.parse_args()
    root = Path(args.repo_root).resolve()
    report = validate(root, load_json((root / args.registry).resolve()))
    output = (root / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
