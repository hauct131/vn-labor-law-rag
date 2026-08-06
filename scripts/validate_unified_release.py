#!/usr/bin/env python3
"""Fail-closed structural validation for the unified candidate release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


EXPECTED_APPENDIX_CODES = {
    "NQ66.18.PL-I.4.C.I",
    "NQ66.18.PL-I.4.C.III",
    "NQ66.18.PL-I.4.C.V",
    "NQ66.18.PL-I.4.C.VII",
    "NQ66.18.PL-I.4.C.VIII",
    "NQ66.18.PL-I.4.C.IX",
}
INTER_ARTICLE_HEADING_RE = re.compile(
    r"^(?:Chương|Mục)\s+(?:[IVXLCDM]+|\d+[A-Za-zĐđ]?)\s*[.．]?$",
    re.IGNORECASE,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def verify_checksum_inventory(release_dir: Path) -> list[str]:
    errors: list[str] = []
    inventory = release_dir / "SHA256SUMS.txt"
    for line_number, line in enumerate(
        inventory.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            expected, relative = line.split("  ", 1)
        except ValueError:
            errors.append(f"Malformed checksum line {line_number}")
            continue
        target = release_dir / relative
        if not target.is_file():
            errors.append(f"Missing checksummed file: {relative}")
        elif sha256_file(target) != expected:
            errors.append(f"Checksum mismatch: {relative}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--release-dir",
        type=Path,
        default=Path(
            "data/releases/labor-law-2026-07-28-candidate"
        ),
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path(
            "data/evaluation/golden_questions_v3_unified_candidate.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/quality/unified_release_validation.json"
        ),
    )
    args = parser.parse_args()

    release = args.release_dir
    manifest = load_json(release / "manifest.json")
    corpus = load_json(release / "articles.json")
    chunks = load_jsonl(release / "chunks.jsonl")
    inventory = load_json(release / "source_inventory.json")
    golden = load_json(args.golden)

    errors = verify_checksum_inventory(release)
    warnings: list[str] = []
    articles = corpus["articles"]
    article_ids = [article["article_id"] for article in articles]
    article_codes = [article["article_code"] for article in articles]
    chunk_ids = [chunk["chunk_id"] for chunk in chunks]
    chunk_keys = [chunk["chunk_key"] for chunk in chunks]
    chunk_article_ids = {
        chunk.get("parent_article_id")
        for chunk in chunks
        if chunk.get("parent_article_id")
    }

    checks = {
        "article_count_513": len(articles) == 513,
        "chunk_count_matches_manifest": (
            len(chunks) == manifest["counts"]["chunks"]
        ),
        "article_ids_unique": len(set(article_ids)) == len(article_ids),
        "article_codes_unique": len(set(article_codes)) == len(article_codes),
        "chunk_ids_unique": len(set(chunk_ids)) == len(chunk_ids),
        "chunk_keys_unique": len(set(chunk_keys)) == len(chunk_keys),
        "all_articles_covered": set(article_ids) == chunk_article_ids,
        "articles_hash_matches": (
            sha256_file(release / "articles.json")
            == manifest["hashes"]["articles_sha256"]
        ),
        "chunks_hash_matches": (
            sha256_file(release / "chunks.jsonl")
            == manifest["hashes"]["chunks_sha256"]
        ),
    }

    document_counts = Counter(
        article["document_number"] for article in articles
    )
    checks["document_count_18"] = len(document_counts) == 18
    checks["bll_220_articles"] = document_counts["18/VBHN-VPQH"] == 220
    checks["nq_2_articles_plus_6_appendix"] = (
        document_counts["66.18/2026/NQ-CP"] == 8
    )

    nq_codes = {
        article["article_code"]
        for article in articles
        if article["document_number"] == "66.18/2026/NQ-CP"
    }
    checks["nq_appendix_codes_exact"] = (
        EXPECTED_APPENDIX_CODES <= nq_codes
        and {"NQ66.18.4", "NQ66.18.6"} <= nq_codes
    )

    bll_220 = next(
        article
        for article in articles
        if article["article_code"] == "20.2.LQ.220"
    )
    bll_220_text = " ".join(
        unit.get("text", "") for unit in bll_220["content_units"]
    )
    checks["no_bll_signature_tail_leak"] = not any(
        marker in bll_220_text
        for marker in (
            "VĂN PHÒNG QUỐC HỘI",
            "XÁC THỰC VĂN BẢN HỢP NHẤT",
            "Lê Quang Mạnh",
        )
    )
    heading_leaks = [
        {
            "article_code": article.get("article_code"),
            "unit_id": unit.get("unit_id"),
            "heading": line.strip(),
        }
        for article in articles
        if article.get("source_adapter") == "official_government_docx"
        for unit in article.get("content_units", [])
        for line in str(unit.get("text", "")).splitlines()
        if INTER_ARTICLE_HEADING_RE.fullmatch(line.strip())
    ]
    checks["no_inter_article_heading_leaks"] = not heading_leaks
    chunking = manifest.get("chunking", {})
    checks["e5_exact_chunking_bound"] = (
        chunking.get("embedding_model")
        == "intfloat/multilingual-e5-large"
        and chunking.get("model_max_tokens") == 512
        and chunking.get("indexer_near_limit_tokens") == 480
        and chunking.get("operational_max_tokens") == 479
        and chunking.get("max_tokens") == 479
        and isinstance(chunking.get("target_tokens"), int)
        and 0 < chunking["target_tokens"] <= 479
        and chunking.get("exact_pre_truncation_count") is True
        and manifest.get("gates", {}).get("e5_token_limit_verified")
        is True
    )

    source_hashes = {
        record["document_number"]: record["sha256"]
        for record in inventory["official_docx_sources"]
    }
    checks["bll_source_hash_expected"] = (
        source_hashes.get("18/VBHN-VPQH")
        == "1386441b1f513defdd55186d7e65b8432dcac87c2e0d78676de25facb9c5e6ff"
    )
    checks["nq_source_hash_expected"] = (
        source_hashes.get("66.18/2026/NQ-CP")
        == "c65df9bd52c10589ea51cc50ea7a36b8ba64d14470343e5d2e309c806b3f130f"
    )
    checks["both_sources_have_signature_parts"] = all(
        record["package"]["digital_signature_parts_present"]
        for record in inventory["official_docx_sources"]
    )
    base_snapshot_audit = inventory["base_corpus"]["snapshot_audit"]
    checks["base_vbpl_16_full_text_hashes_match"] = (
        base_snapshot_audit["document_count"] == 16
        and base_snapshot_audit["full_text_hash_match_count"] == 16
    )
    base_vbpl_provenance_verified = (
        base_snapshot_audit["document_count"] == 16
        and base_snapshot_audit["full_text_hash_match_count"] == 16
        and base_snapshot_audit["fully_verifiable_count"] == 16
        and base_snapshot_audit["status"] == "complete"
        and manifest["gates"]["source_hashes_verified"] is True
        and manifest["gates"][
            "base_vbpl_snapshot_verification_passed"
        ] is True
        and manifest["gates"]["base_vbpl_snapshot_status"] == "complete"
    )
    legacy_provenance_gap_disclosed = (
        base_snapshot_audit["fully_verifiable_count"] == 0
        and base_snapshot_audit["status"] == "incomplete_supplied_archive"
        and manifest["gates"]["source_hashes_verified"] is False
        and manifest["gates"][
            "base_vbpl_snapshot_verification_passed"
        ] is False
    )
    checks["base_vbpl_provenance_verified_or_gap_disclosed"] = (
        base_vbpl_provenance_verified
        or legacy_provenance_gap_disclosed
    )

    chunks_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    golden_missing_ids: dict[str, list[str]] = {}
    golden_wrong_code_ids: dict[str, list[str]] = {}
    for question in golden["questions"]:
        expected_codes = set(question.get("expected_article_codes", []))
        missing = [
            chunk_id
            for chunk_id in question.get("evidence_chunk_ids", [])
            if chunk_id not in chunks_by_id
        ]
        wrong = [
            chunk_id
            for chunk_id in question.get("evidence_chunk_ids", [])
            if chunk_id in chunks_by_id
            and chunks_by_id[chunk_id].get("article_code")
            not in expected_codes
        ]
        if missing:
            golden_missing_ids[question["id"]] = missing
        if wrong:
            golden_wrong_code_ids[question["id"]] = wrong
    checks["golden_all_chunk_ids_exist"] = not golden_missing_ids
    checks["golden_evidence_codes_match"] = not golden_wrong_code_ids
    checks["golden_release_hash_matches"] = (
        golden["corpus"]["sha256"]
        == manifest["hashes"]["chunks_sha256"]
    )

    config_text = Path("backend/app/core/config.py").read_text(
        encoding="utf-8"
    )
    docker_text = Path("docker-compose.yml").read_text(encoding="utf-8")
    corpus_config = load_json(Path("config/vbpl_corpus.json"))
    configured_nq = next(
        document
        for document in corpus_config["documents"]
        if document["document_number"] == "66.18/2026/NQ-CP"
    )
    configured_count = re.search(
        r"retrieval_expected_chunks:\s*int\s*=\s*(\d+)",
        config_text,
    )
    configured_hash_parts = re.search(
        r"retrieval_corpus_sha256:\s*str\s*=\s*\(\s*"
        r'"([0-9a-f]+)"\s*"([0-9a-f]+)"',
        config_text,
        re.MULTILINE,
    )
    checks["backend_chunk_count_bound"] = (
        configured_count is not None
        and int(configured_count.group(1)) == len(chunks)
    )
    checks["backend_chunk_hash_bound"] = (
        configured_hash_parts is not None
        and "".join(configured_hash_parts.groups())
        == manifest["hashes"]["chunks_sha256"]
    )
    checks["docker_reads_release_chunks"] = (
        "/app/data/releases/labor-law-2026-07-28-candidate/chunks.jsonl"
        in docker_text
        and "/app/data/processed/legal_chunks.jsonl" not in docker_text
    )
    checks["nq_config_expected_articles_7"] = (
        configured_nq["expected_articles"] == 7
    )
    checks["pytest_qdrant_summary_not_production"] = not Path(
        "data/processed/qdrant_index/summary.json"
    ).exists()

    for name, passed in checks.items():
        if not passed:
            errors.append(f"Failed check: {name}")
    if not manifest["gates"].get("e5_token_limit_verified"):
        warnings.append(
            "E5 tokenizer hard-limit audit has not run in this offline build."
        )
    if not manifest["gates"].get(
        "base_vbpl_snapshot_verification_passed"
    ):
        warnings.append(
            "Base VBPL snapshot provenance is incomplete or technical "
            "checksum verification did not pass."
        )
    if not manifest["gates"].get("authority_review_passed"):
        warnings.append(
            "Legal authority review is pending; production publication is blocked."
        )

    report = {
        "schema_version": "unified-release-validation-v1",
        "status": "PASS" if not errors else "FAIL",
        "release_id": manifest["release_id"],
        "checks": checks,
        "counts": {
            "documents": len(document_counts),
            "article_containers": len(articles),
            "chunks": len(chunks),
            "golden_questions": len(golden["questions"]),
        },
        "document_counts": dict(sorted(document_counts.items())),
        "errors": errors,
        "warnings": warnings,
        "golden_missing_ids": golden_missing_ids,
        "golden_wrong_code_ids": golden_wrong_code_ids,
        "inter_article_heading_leaks": heading_leaks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
