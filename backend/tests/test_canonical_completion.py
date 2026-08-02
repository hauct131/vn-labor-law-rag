from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / "data/releases/labor-law-canonical-20260727-candidate"


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_builder_module():
    path = ROOT / "scripts/complete_canonical_corpus.py"
    spec = importlib.util.spec_from_file_location("complete_canonical_corpus", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_registry_is_complete_and_hash_bound() -> None:
    registry = load_json(ROOT / "data/governance/source_registry.json")
    assert registry["summary"] == {
        "document_count": 18,
        "vbpl_snapshot_count": 16,
        "official_docx_count": 2,
        "included_container_count": 513,
        "integrity_failures": 0,
        "technical_unresolved_count": 0,
        "authority_review_pending": 18,
    }
    for document in registry["documents"]:
        acquisition = document["acquisition"]
        integrity = document["integrity"]
        if document["source_kind"] == "vbpl_snapshot":
            path = ROOT / acquisition["primary_file_path"]
            expected = integrity["content_sha256"]
        else:
            path = ROOT / acquisition["source_file_path"]
            expected = integrity["source_file_sha256"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_phapdien_is_reference_only_and_all_rows_are_classified() -> None:
    report = load_json(ROOT / "data/governance/phapdien_20_2_coverage_report.json")
    assert report["role_policy"]["direct_merge_allowed"] is False
    counts = report["summary"]["classification_counts"]
    assert sum(counts.values()) == 477
    assert counts["covered_by_document_and_article"] == 233


def test_canonical_release_passes_e5_gate_but_fails_closed_for_authority_review() -> None:
    report = load_json(RELEASE / "STRICT_GATE_REPORT.json")
    assert report["technical_candidate_passed"] is True
    assert report["production_publishable"] is False
    assert report["counts"]["documents"] == 18
    assert report["counts"]["canonical_containers"] == 510
    chunk_count = sum(1 for line in (RELEASE / "canonical_chunks.jsonl").read_text(encoding="utf-8").splitlines() if line)
    assert report["counts"]["canonical_chunks"] == chunk_count
    assert report["counts"]["chunk_trace_errors"] == 0
    assert report["counts"]["known_source_gaps"] == 0
    assert report["counts"]["changed_articles"] >= 3
    assert report["gates"]["current_text_source_gaps_resolved"] is True
    assert report["gates"]["effect_evidence_hashes_verified"] is True
    assert report["gates"]["exact_e5_audit_passed"] is True
    assert report["gates"]["authority_review_passed"] is False
    assert report["blockers"] == [
        "Obtain authorized legal-effect approval bound to manifest SHA-256."
    ]


def test_exact_e5_audit_is_bound_to_canonical_bytes() -> None:
    chunks_path = RELEASE / "canonical_chunks.jsonl"
    summary_path = RELEASE / "e5_audit/summary.json"
    binding = load_json(RELEASE / "e5_audit_binding.json")
    summary = load_json(summary_path)
    report = load_json(RELEASE / "STRICT_GATE_REPORT.json")
    manifest = load_json(RELEASE / "manifest.json")

    chunks_sha = hashlib.sha256(chunks_path.read_bytes()).hexdigest()
    chunk_count = sum(
        1 for line in chunks_path.read_text(encoding="utf-8").splitlines() if line
    )
    assert summary["input_sha256"] == chunks_sha == binding["canonical_chunks_sha256"]
    assert summary["chunk_count"] == chunk_count == binding["canonical_chunk_count"]
    assert summary["model_name"] == "intfloat/multilingual-e5-large"
    assert summary["exact_measurement"] is True
    assert summary["risk_counts"]["strictly_over_model_limit_count"] == 0
    assert binding["audit_summary_sha256"] == hashlib.sha256(summary_path.read_bytes()).hexdigest()
    assert binding["verified"] is True
    assert binding["errors"] == []
    assert report["gate_details"]["exact_e5_audit"] == binding
    assert manifest["e5_audit_binding"] == binding


def test_stale_exact_e5_audit_fails_closed(tmp_path: Path) -> None:
    builder = load_builder_module()
    chunks_path = tmp_path / "canonical_chunks.jsonl"
    chunks_path.write_text('{"chunk_id":"one"}\n', encoding="utf-8")
    audit_path = tmp_path / "summary.json"
    audit_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "input_path": str(chunks_path),
                "input_sha256": "0" * 64,
                "chunk_count": 1,
                "model_name": "intfloat/multilingual-e5-large",
                "actual_model_max_tokens": 512,
                "prefix_string": "passage: ",
                "exact_measurement": True,
                "risk_counts": {
                    "near_or_above_count": 0,
                    "at_model_limit_count": 0,
                    "strictly_over_model_limit_count": 0,
                },
                "validation": {"is_valid": True, "error_count": 0},
            }
        ),
        encoding="utf-8",
    )

    passed, binding = builder.verify_exact_e5_audit(
        tmp_path, audit_path, chunks_path, expected_chunk_count=1
    )
    assert passed is False
    assert binding["verified"] is False
    assert any("input_sha256 mismatch" in error for error in binding["errors"])


def test_excluded_provisions_do_not_survive() -> None:
    decisions = load_json(ROOT / "config/legal_effect_decisions_20260727.json")
    corpus = load_json(RELEASE / "canonical_articles.json")
    unit_ids = {
        unit["unit_id"]
        for article in corpus["articles"]
        for unit in article.get("content_units", [])
    }
    assert not [
        unit_id
        for unit_id in unit_ids
        if any(
            unit_id == prefix
            or unit_id.startswith(prefix if prefix.endswith("|") else f"{prefix}|")
            for prefix in decisions["exclude_unit_prefixes"]
        )
    ]
    assert "vbpl:vn:09-2020-tt-bldtbxh:article:6|clause=2" in unit_ids
    article = next(
        row
        for row in corpus["articles"]
        if row["document_number"] == "09/2020/TT-BLĐTBXH" and row["article_number"] == 6
    )
    clause = next(row for row in article["content_units"] if row["unit_id"].endswith("|clause=2"))
    assert "sổ hộ khẩu" not in clause["text"]


def test_locked_regression_set_keeps_all_evidence() -> None:
    golden = load_json(ROOT / "data/evaluation/golden_questions_v3_canonical_20260727.json")
    audit = load_json(RELEASE / "golden_regression_audit.json")
    assert golden["locked"] is True
    assert len(golden["questions"]) == 45
    assert audit["evidence_reference_count"] == sum(
        len(question["evidence_chunk_ids"]) for question in golden["questions"]
    )
    assert audit["evidence_missing_count"] == 0
    assert audit["status"] == "PASS"


def test_amendment_snapshots_are_hash_bound() -> None:
    decisions = load_json(ROOT / "config/legal_effect_decisions_20260727.json")
    evidence = {row["id"]: row for row in decisions["evidence"]}
    expected = {
        "nd35-2022": "e74b6cf08e204c105130e34937044475f27a59d9559bde11720551ef37ef0e35",
        "nd10-2024": "9bbb4c8c7ee072b5b79d89e2444ef6a8686922557719b0138881bd39c6fd30fe",
    }
    for evidence_id, expected_hash in expected.items():
        row = evidence[evidence_id]
        assert row["hash_binding_required"] is True
        path = ROOT / row["local_snapshot"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash == row["sha256"]


def test_amendment_snapshot_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    builder = load_builder_module()
    snapshot = tmp_path / "source.pdf"
    snapshot.write_bytes(b"tampered")
    decisions = {
        "evidence": [
            {
                "id": "required-source",
                "local_snapshot": "source.pdf",
                "sha256": "0" * 64,
                "hash_binding_required": True,
            }
        ]
    }
    with pytest.raises(ValueError, match="evidence snapshot hash mismatch"):
        builder.verify_effect_evidence(tmp_path, decisions)


def test_unit_rule_matching_has_a_structural_boundary() -> None:
    builder = load_builder_module()
    rule = "vbpl:vn:145-2020-nd-cp:article:93|clause=1"
    assert builder.unit_id_matches_rule(rule, rule)
    assert builder.unit_id_matches_rule(f"{rule}|point=a", rule)
    assert not builder.unit_id_matches_rule(
        "vbpl:vn:145-2020-nd-cp:article:93|clause=10",
        rule,
    )


def test_nd35_and_nd10_current_text_is_present_with_provenance() -> None:
    corpus = load_json(RELEASE / "canonical_articles.json")
    review = load_json(RELEASE / "legal_effect_review.json")
    articles = {
        (row["document_number"], int(row["article_number"])): row
        for row in corpus["articles"]
        if row.get("document_number") == "145/2020/NĐ-CP"
        and row.get("article_number") is not None
    }
    expected_units = {
        4: [
            "vbpl:vn:145-2020-nd-cp:article:4|clause=4",
            "vbpl:vn:145-2020-nd-cp:article:4|clause=4|point=a",
            "vbpl:vn:145-2020-nd-cp:article:4|clause=4|point=b",
        ],
        31: ["vbpl:vn:145-2020-nd-cp:article:31|clause=5"],
        62: ["vbpl:vn:145-2020-nd-cp:article:62|clause=4"],
    }
    for article_number, added_ids in expected_units.items():
        units = articles[("145/2020/NĐ-CP", article_number)]["content_units"]
        unit_ids = [unit["unit_id"] for unit in units]
        indices = [unit_ids.index(unit_id) for unit_id in added_ids]
        assert indices == sorted(indices)

    article31 = articles[("145/2020/NĐ-CP", 31)]
    clause2 = next(
        unit for unit in article31["content_units"] if unit["unit_id"].endswith("|clause=2")
    )
    assert "Ban quản lý khu công nghiệp, khu kinh tế" in clause2["text"]

    assert review["known_source_gaps"] == []
    assert set(review["effect_evidence_verification"]) == {"nd35-2022", "nd10-2024"}
    changed_provenance = {
        row["unit_id"]: row["evidence_id"]
        for row in review["transformations"]
        if row["action"] in {"replace_text", "insert_unit"}
    }
    assert changed_provenance["vbpl:vn:145-2020-nd-cp:article:31|clause=2"] == "nd35-2022"
    for added_ids in expected_units.values():
        for unit_id in added_ids:
            assert changed_provenance[unit_id] == "nd10-2024"


def test_release_checksums_verify() -> None:
    for line in (RELEASE / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        expected, filename = line.split("  ", 1)
        assert hashlib.sha256((RELEASE / filename).read_bytes()).hexdigest() == expected
