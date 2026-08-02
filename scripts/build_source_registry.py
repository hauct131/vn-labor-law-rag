#!/usr/bin/env python3
"""Build the Step-2 draft source registry from an audited unified release."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HIGH_PRIORITY_REVIEW = {
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


def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def official_source_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    documents = payload.get("documents", {})
    require(isinstance(documents, dict), "official_legal_sources.documents must be an object")
    result: dict[str, dict[str, Any]] = {}
    for item in documents.values():
        number = item.get("document_number")
        if number:
            result[number] = item
    return result


def article_summary(articles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for article in articles:
        grouped[article["document_number"]].append(article)
    result: dict[str, dict[str, Any]] = {}
    for number, rows in grouped.items():
        first = rows[0]
        result[number] = {
            "count": len(rows),
            "title": first.get("document_title"),
            "source_item_id": first.get("source_item_id"),
            "issued_at": first.get("issued_at"),
            "effective_from": first.get("effective_from"),
            "effective_to": first.get("effective_to"),
            "source_urls": first.get("source_urls") or [],
            "source_sha256": first.get("source_sha256"),
        }
    return result


def build_registry(
    repo_root: Path,
    release_dir: Path,
    run_manifest_path: Path,
    official_sources_path: Path,
) -> dict[str, Any]:
    release_manifest = load_json(release_dir / "manifest.json")
    gates = release_manifest.get("gates", {})
    require(gates.get("source_hashes_verified") is True, "release source_hashes_verified is not true")
    require(
        gates.get("official_docx_source_hashes_verified") is True,
        "release official_docx_source_hashes_verified is not true",
    )
    require(
        gates.get("base_vbpl_snapshot_verification_passed") is True,
        "release base_vbpl_snapshot_verification_passed is not true",
    )

    article_payload = load_json(release_dir / "articles.json")
    articles = article_payload["articles"]
    summaries = article_summary(articles)
    require(len(summaries) == 18, f"expected 18 document numbers, got {len(summaries)}")
    require(len(articles) == 513, f"expected 513 article containers, got {len(articles)}")

    inventory = load_json(release_dir / "source_inventory.json")
    snapshot_audit = {
        row["document_number"]: row
        for row in inventory.get("base_corpus", {}).get("snapshot_audit", {}).get("records", [])
    }
    official_sources = official_source_map(load_json(official_sources_path))
    run_manifest = load_json(run_manifest_path)
    run_rows = run_manifest.get("results", [])
    require(len(run_rows) == 16, f"expected 16 VBPL run results, got {len(run_rows)}")

    review_path = release_dir / "legal_effect_review.json"
    review_payload = load_json(review_path) if review_path.exists() else {"documents": []}
    technical_reviews = {
        row["document_number"]: row for row in review_payload.get("documents", [])
    }

    documents: list[dict[str, Any]] = []
    for row in run_rows:
        number = row["document_number"]
        require(number in summaries, f"{number}: missing from release articles")
        snapshot_dir = repo_root / row["snapshot_dir"]
        snapshot_manifest_path = snapshot_dir / "manifest.json"
        full_text_path = snapshot_dir / "full_text.txt"
        require(snapshot_manifest_path.is_file(), f"{number}: missing {snapshot_manifest_path}")
        require(full_text_path.is_file(), f"{number}: missing {full_text_path}")
        snapshot = load_json(snapshot_manifest_path)
        declared_content_hash = snapshot["content_hashes"]["full_text_text_sha256"]
        actual_content_hash = sha256(full_text_path)
        audit = snapshot_audit.get(number, {})
        require(actual_content_hash == declared_content_hash, f"{number}: full_text.txt hash mismatch")
        require(audit.get("technical_verification_passed") is True, f"{number}: snapshot audit did not pass")
        doc_meta = snapshot["document"]
        source_meta = snapshot["source"]
        official = official_sources.get(number, {})
        summary = summaries[number]
        require(summary["source_sha256"] == declared_content_hash, f"{number}: release/source snapshot hash mismatch")
        documents.append(
            {
                "document_number": number,
                "title": doc_meta.get("title") or summary["title"],
                "source_kind": "vbpl_snapshot",
                "provider": source_meta.get("provider"),
                "source_item_id": str(doc_meta.get("item_id")),
                "official_identity_url": official.get("canonical_url"),
                "acquisition": {
                    "detail_url": source_meta.get("detail_url"),
                    "gateway_url": source_meta.get("gateway_url"),
                    "retrieved_at": snapshot.get("retrieved_at"),
                    "snapshot_path": rel(snapshot_dir, repo_root),
                    "primary_file_path": rel(full_text_path, repo_root),
                    "method": source_meta.get("acquisition_method"),
                },
                "integrity": {
                    "content_sha256": declared_content_hash,
                    "content_hash_verified": True,
                    "snapshot_manifest_sha256": sha256(snapshot_manifest_path),
                    "snapshot_audit_verified": True,
                },
                "observed_legal_metadata": {
                    "issued_at": doc_meta.get("issued_at"),
                    "effective_from": doc_meta.get("effective_from"),
                    "effective_to": doc_meta.get("effective_to"),
                    "portal_status": doc_meta.get("legal_status"),
                    "relations": doc_meta.get("relations", {}),
                },
                "corpus_scope": {"included_unit_count": summary["count"]},
                "legal_review": {
                    "status": "pending",
                    "decision": "unresolved",
                    "priority": "high" if number in HIGH_PRIORITY_REVIEW else "normal",
                    "reviewed_as_of": None,
                    "reviewer": None,
                    "authority_review_passed": False,
                    "evidence_urls": [],
                    "notes": "Review legal effect at document/provision level in Step 3.",
                },
            }
        )

    docx_sources = inventory.get("official_docx_sources", [])
    require(len(docx_sources) == 2, f"expected 2 DOCX sources, got {len(docx_sources)}")
    raw_snapshot_by_number = {
        row["document_number"]: row for row in inventory.get("raw_snapshots", [])
    }
    for item in docx_sources:
        number = item["document_number"]
        require(number in summaries, f"{number}: missing from release articles")
        source_file = release_dir / item["release_path"]
        require(source_file.is_file(), f"{number}: missing {source_file}")
        actual_hash = sha256(source_file)
        require(actual_hash == item["sha256"], f"{number}: DOCX hash mismatch")
        summary = summaries[number]
        review = technical_reviews.get(number)
        raw = raw_snapshot_by_number.get(number, {})
        documents.append(
            {
                "document_number": number,
                "title": summary["title"],
                "source_kind": "user_supplied_official_docx",
                "provider": item.get("provider"),
                "source_item_id": item.get("source_item_id"),
                "official_identity_url": item.get("official_page_url"),
                "acquisition": {
                    "source_file_path": rel(source_file, repo_root),
                    "raw_snapshot_path": raw.get("snapshot_dir"),
                    "acquisition_claim": (
                        "DOCX supplied to the pipeline, hash-bound, and identity-checked "
                        "against the official government page."
                    ),
                    "direct_download_verified": False,
                },
                "integrity": {
                    "source_file_sha256": item["sha256"],
                    "source_file_hash_verified": True,
                    "raw_snapshot_manifest_sha256": raw.get("manifest_sha256"),
                    "digital_signature_parts_present": item.get("package", {}).get(
                        "digital_signature_parts_present"
                    ),
                    "cryptographic_signature_validation": item.get("package", {}).get(
                        "cryptographic_signature_validation"
                    ),
                },
                "observed_legal_metadata": {
                    "issued_at": summary["issued_at"],
                    "effective_from": summary["effective_from"],
                    "effective_to": summary["effective_to"],
                    "portal_status": None,
                    "relations": {},
                },
                "corpus_scope": {"included_unit_count": summary["count"]},
                "legal_review": {
                    "status": "technical_review_recorded" if review else "pending",
                    "decision": review.get("status_at_law_as_of") if review else "unresolved",
                    "priority": "normal",
                    "reviewed_as_of": release_manifest.get("law_as_of") if review else None,
                    "reviewer": "pipeline_technical_review" if review else None,
                    "authority_review_passed": False,
                    "evidence_urls": [item.get("official_page_url")],
                    "notes": review.get("basis") if review else "Pending legal review.",
                },
            }
        )

    documents.sort(key=lambda item: item["document_number"])
    kind_counts = Counter(item["source_kind"] for item in documents)
    require(len(documents) == 18, f"expected 18 registry documents, got {len(documents)}")
    require(kind_counts["vbpl_snapshot"] == 16, "registry must contain 16 VBPL snapshots")
    require(kind_counts["user_supplied_official_docx"] == 2, "registry must contain 2 DOCX sources")
    require(sum(item["corpus_scope"]["included_unit_count"] for item in documents) == 513, "unit total is not 513")

    return {
        "schema_version": "labor-law-source-registry-v1",
        "registry_status": "draft_pending_legal_review",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_release": {
            "release_id": release_manifest.get("release_id"),
            "release_dir": rel(release_dir, repo_root),
            "law_as_of": release_manifest.get("law_as_of"),
            "release_status": release_manifest.get("release_status"),
            "technical_source_gates_passed": True,
            "authority_review_passed": False,
        },
        "summary": {
            "document_count": 18,
            "vbpl_snapshot_count": 16,
            "official_docx_count": 2,
            "included_unit_count": 513,
            "legal_review_pending": sum(
                item["legal_review"]["status"] == "pending" for item in documents
            ),
            "authority_review_pending": 18,
            "high_priority_review_count": len(HIGH_PRIORITY_REVIEW),
        },
        "documents": documents,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--release-dir", required=True)
    parser.add_argument("--run-manifest", default="data/raw/vbpl/run_manifest.json")
    parser.add_argument("--official-sources", default="data/reference/official_legal_sources.json")
    parser.add_argument("--output", default="data/governance/source_registry.draft.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.repo_root).resolve()
    release_dir = (root / args.release_dir).resolve()
    registry = build_registry(
        root,
        release_dir,
        (root / args.run_manifest).resolve(),
        (root / args.official_sources).resolve(),
    )
    output = (root / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output}")
    print(json.dumps(registry["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
