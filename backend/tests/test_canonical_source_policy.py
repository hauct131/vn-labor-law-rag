from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from scripts import canonical_source_policy as policy
from scripts import congbao_docx as gazette


def project_config() -> Path:
    return Path(__file__).resolve().parents[2] / "config/vbpl_corpus.json"


def test_policy_resolves_seventeen_gazette_and_one_explicit_vbpl_fallback() -> None:
    gazette_calls: list[str] = []
    vbpl_calls: list[str] = []

    def fetch_gazette(item: Mapping[str, Any]) -> Mapping[str, Any]:
        number = str(item["document_number"])
        gazette_calls.append(number)
        return {
            "status": "created",
            "snapshot_dir": f"/gazette/{number}",
            "source_path": f"/normalized/{number}.docx",
            "manifest": {
                "content_hashes": {"source_fingerprint_sha256": "a" * 64}
            },
        }

    def fetch_vbpl(item: Mapping[str, Any]) -> Mapping[str, Any]:
        number = str(item["document_number"])
        vbpl_calls.append(number)
        return {
            "status": "skipped_valid",
            "snapshot_dir": f"/vbpl/{number}",
            "manifest": {
                "content_hashes": {"full_text_text_sha256": "b" * 64}
            },
        }

    report = policy.run_policy(
        project_config(),
        fetch_gazette=fetch_gazette,
        fetch_vbpl=fetch_vbpl,
    )
    assert report["status"] == "PASS"
    assert report["documents_requested"] == 18
    assert report["official_gazette_documents"] == 17
    assert report["vbpl_fallback_documents"] == 1
    assert len(gazette_calls) == 17
    assert vbpl_calls == ["10/2020/TT-BLĐTBXH"]
    assert report["one_canonical_snapshot_per_document"] is True
    assert report["production_promotion_performed"] is False


def _write_single_document_config(path: Path) -> None:
    payload = {
        "canonical_source_policy": {
            "name": policy.POLICY_NAME,
            "priority": ["official_gazette_word", "vbpl"],
            "fallback_on": [
                "gazette_record_not_found",
                "word_attachment_not_found",
            ],
        },
        "documents": [
            {
                "document_number": "135/2020/NĐ-CP",
                "canonical_document_id": "vn:135-2020-nd-cp",
                "title": "Tuổi nghỉ hưu",
                "expected_articles": 9,
                "item_id": "152734",
                "gazette_page_url": "https://congbao.chinhphu.vn/van-ban/test.htm",
                "canonical_source_policy": {
                    "preferred_adapter": "official_gazette_word",
                    "fallback_adapter": "vbpl",
                },
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_identity_or_integrity_error_never_falls_back(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    _write_single_document_config(config)
    fallback_calls = 0

    def fail_identity(_: Mapping[str, Any]) -> Mapping[str, Any]:
        raise gazette.CongbaoError("Metadata document-number conflict")

    def fetch_vbpl(_: Mapping[str, Any]) -> Mapping[str, Any]:
        nonlocal fallback_calls
        fallback_calls += 1
        return {}

    report = policy.run_policy(
        config,
        fetch_gazette=fail_identity,
        fetch_vbpl=fetch_vbpl,
    )
    assert report["status"] == "FAIL"
    assert fallback_calls == 0
    assert report["failures"][0]["reason"] == (
        "fail_closed_non_availability_error"
    )


def test_missing_word_attachment_can_use_allowlisted_vbpl_fallback(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.json"
    _write_single_document_config(config)

    def unavailable(_: Mapping[str, Any]) -> Mapping[str, Any]:
        raise gazette.CongbaoUnavailableError(
            "word_attachment_not_found", "no Word attachment"
        )

    def fetch_vbpl(_: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "status": "created",
            "snapshot_dir": "/vbpl/135",
            "manifest": {
                "content_hashes": {"full_text_text_sha256": "c" * 64}
            },
        }

    report = policy.run_policy(
        config,
        fetch_gazette=unavailable,
        fetch_vbpl=fetch_vbpl,
    )
    assert report["status"] == "PASS"
    assert report["vbpl_fallback_documents"] == 1
    assert report["results"][0]["fallback_reason"] == (
        "word_attachment_not_found"
    )
