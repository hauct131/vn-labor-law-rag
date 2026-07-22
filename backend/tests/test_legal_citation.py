"""Tests for deterministic, human-readable legal source labels."""

from backend.app.services.legal_citation import build_citation_metadata


def test_labor_code_citation_uses_canonical_title_and_number() -> None:
    citation = build_citation_metadata({
        "article_code": "20.2.LQ.113",
        "source_type": "LQ",
        "source_note_text": (
            "(Điều 113 Bộ luật số 45/2019/QH14, có hiệu lực "
            "thi hành kể từ ngày 01/01/2021)"
        ),
    })

    assert citation.article_number == "113"
    assert citation.document_title == "Bộ luật Lao động"
    assert citation.document_number == "45/2019/QH14"
    assert citation.label == (
        "Điều 113 Bộ luật Lao động số 45/2019/QH14"
    )


def test_decree_citation_uses_source_note_metadata() -> None:
    citation = build_citation_metadata({
        "article_code": "20.2.NĐ.3.66",
        "source_type": "NĐ",
        "source_note_text": (
            "(Điều 66 Nghị định số 145/2020/NĐ-CP, có hiệu lực "
            "thi hành kể từ ngày 01/02/2021)"
        ),
    })

    assert citation.article_number == "66"
    assert citation.document_title == "Nghị định"
    assert citation.document_number == "145/2020/NĐ-CP"
    assert citation.label == "Điều 66 Nghị định số 145/2020/NĐ-CP"


def test_direct_metadata_takes_priority_over_note_parsing() -> None:
    citation = build_citation_metadata({
        "article_code": "20.2.TT.4.7",
        "article_number": "7",
        "document_title": "Thông tư",
        "document_number": "18/2021/TT-BLĐTBXH",
        "source_note_text": "dữ liệu ghi chú không đầy đủ",
    })

    assert citation.label == (
        "Điều 7 Thông tư số 18/2021/TT-BLĐTBXH"
    )


def test_missing_document_metadata_falls_back_without_exposing_chunk_id() -> None:
    citation = build_citation_metadata({
        "article_code": "20.2.NĐ.3.66",
        "source_type": "NĐ",
    })

    assert citation.article_number == "66"
    assert citation.label == "Điều 66 Nghị định"
