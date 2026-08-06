"""Unit and integration tests for scripts/build_vbpl_articles.py.

Run:
    pytest backend/tests/test_vbpl_article_adapter.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.build_vbpl_articles import (  # noqa: E402
    ADAPTER_VERSION,
    _stable_article_id,
    _make_unit_id,
    find_article_headings,
    parse_include_articles,
    parse_content_units,
    select_main_sequence,
    select_content_sequence,
    build_articles_from_snapshot,
    find_appendix_boundary,
    find_appendix_info,
    find_administrative_tail_boundary,
    find_administrative_tail_info,
    main,
)


# ===========================================================================
# Fixtures
# ===========================================================================

MINIMAL_FULL_TEXT = """\
CHÍNH PHỦ
Số: 135/2020/NĐ-CP
NGHỊ ĐỊNH
Căn cứ Bộ luật Lao động.
Điều 1. Phạm vi điều chỉnh
Nghị định này quy định chi tiết Điều 169 của Bộ luật Lao động.
Điều 2. Đối tượng áp dụng
1. Người lao động và người sử dụng lao động.
2. Cơ quan, tổ chức và cá nhân có liên quan.
Điều 3. Hiệu lực thi hành
Nghị định này có hiệu lực từ ngày 01 tháng 01 năm 2021.
"""

MINIMAL_MANIFEST = {
    "schema_version": "vbpl-source-snapshot-v3",
    "document": {
        "document_number": "135/2020/NĐ-CP",
        "item_id": "152734",
        "issued_at": "2020-11-18",
        "effective_from": "2021-01-01",
        "effective_to": None,
        "issuing_authority": "Chính phủ",
        "title": "Nghị định 135",
        "type_vb": {"code": "NĐ"},
    },
    "content_hashes": {
        "full_text_text_sha256": "deadbeef",
    },
    "source": {
        "detail_url": "https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=152734",
    },
    "retrieved_at": "2026-07-24T10:39:57+00:00",
}

MINIMAL_CONFIG_DOC = {
    "document_number": "135/2020/NĐ-CP",
    "canonical_document_id": "vn:135-2020-nd-cp",
    "expected_articles": 3,
    "include_articles": "1-3",
    "source_adapter": "vbpl",
    "item_id": "152734",
}


def _write_snapshot(tmp: Path, full_text: str = MINIMAL_FULL_TEXT, manifest: dict | None = None) -> Path:
    """Write a minimal snapshot directory."""
    snap = tmp / "snapshot"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "full_text.txt").write_text(full_text, encoding="utf-8")
    m = manifest if manifest is not None else MINIMAL_MANIFEST
    (snap / "manifest.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    return snap


# ===========================================================================
# 1. find_article_headings: only at start of line
# ===========================================================================

def test_heading_at_start_of_line():
    text = "Điều 1. Phạm vi điều chỉnh\nNội dung.\n"
    headings = find_article_headings(text)
    assert len(headings) == 1
    num, title, offset = headings[0]
    assert num == 1
    assert title == "Phạm vi điều chỉnh"
    assert offset == 0


def test_heading_not_in_midline():
    """'Điều X' inside a sentence must NOT become a heading."""
    text = "Theo Điều 8 Nghị định số 145/2020/NĐ-CP thì người lao động...\n"
    headings = find_article_headings(text)
    assert headings == []


def test_heading_mid_document():
    text = "Chương I\nĐiều 1. Một\nNội dung.\nĐiều 2. Hai\nNội dung 2.\n"
    headings = find_article_headings(text)
    assert [h[0] for h in headings] == [1, 2]


def test_heading_full_text():
    headings = find_article_headings(MINIMAL_FULL_TEXT)
    assert [h[0] for h in headings] == [1, 2, 3]


# ===========================================================================
# 2. No reference Điều inside sentence
# ===========================================================================

def test_inline_dieu_reference_not_captured():
    text = (
        "theo quy định tại khoản 1 Điều 5 của Bộ luật Lao động\n"
        "Điều 5. Tiêu đề thật\n"
        "Nội dung thật.\n"
    )
    headings = find_article_headings(text)
    assert len(headings) == 1
    assert headings[0][0] == 5


# ===========================================================================
# 3. select_main_sequence: correct sequence selection
# ===========================================================================

def test_select_main_sequence_no_duplicates():
    headings = [
        (1, "Một", 0),
        (2, "Hai", 50),
        (3, "Ba", 100),
        (1, "Một lại", 200),   # appendix repeat
        (2, "Hai lại", 250),
    ]
    seq = select_main_sequence(headings, {1, 2, 3})
    assert [h[0] for h in seq] == [1, 2, 3]
    assert seq[0][2] == 0
    assert seq[1][2] == 50


def test_select_main_sequence_subset():
    headings = [(1, "A", 0), (2, "B", 10), (3, "C", 20), (4, "D", 30)]
    seq = select_main_sequence(headings, {2, 3})
    assert [h[0] for h in seq] == [2, 3]


def test_select_main_sequence_toc_before_content():
    """When a TOC appears before main body, parser must pick the body chain."""
    full_text = (
        "MỤC LỤC\n"
        "Điều 1. Phạm vi (trang 1)\n"
        "Điều 2. Đối tượng (trang 2)\n"
        "NỘI DUNG CHÍNH\n"
        "Điều 1. Phạm vi điều chỉnh chính\n"
        "1. Nội dung chi tiết của Điều 1.\n"
        "Điều 2. Đối tượng áp dụng chính\n"
        "1. Nội dung chi tiết của Điều 2.\n"
    )
    headings = find_article_headings(full_text)
    seq = select_content_sequence(headings, {1, 2}, full_text)
    assert len(seq) == 2
    assert seq[0][1] == "Phạm vi điều chỉnh chính"
    assert seq[1][1] == "Đối tượng áp dụng chính"


def test_select_main_sequence_appendix_repeats_ignored():
    """Main sequence 1-10 with repeated Articles 9-11 in appendix form template."""
    full_text = (
        "Điều 1. Đầu\n1. Nội dung 1.\n"
        "Điều 9. Chín chính\n1. Nội dung 9 chính.\n"
        "Điều 10. Mười chính\n1. Khi hợp đồng bị tuyên bố vô hiệu.\n"
        "Điều 11. Mười một chính\n1. Nội dung 11.\n"
        "PHỤ LỤC FORM\n"
        "Điều 9. Mẫu 9\n"
        "Điều 10. Thỏa thuận khác (nếu có)\n"
        "Điều 11. Mẫu 11\n"
    )
    headings = find_article_headings(full_text)
    seq = select_content_sequence(headings, {1, 9, 10, 11}, full_text)
    assert len(seq) == 4
    art10 = next(h for h in seq if h[0] == 10)
    assert art10[1] == "Mười chính"


# ===========================================================================
# 4. parse_content_units: preamble
# ===========================================================================

def test_preamble_extracted():
    body = "Điều này quy định về phạm vi và đối tượng.\n"
    units = parse_content_units(body, "vn:test", 1)
    assert len(units) == 1
    assert units[0]["unit_type"] == "preamble"
    assert "phạm vi" in units[0]["text"]


def test_preamble_before_clause():
    body = "Giới thiệu chung.\n1. Khoản đầu tiên.\n"
    units = parse_content_units(body, "vn:test", 2)
    types = [u["unit_type"] for u in units]
    assert "preamble" in types
    assert "clause" in types
    preamble = next(u for u in units if u["unit_type"] == "preamble")
    assert "Giới thiệu" in preamble["text"]


# ===========================================================================
# 5. parse_content_units: clauses
# ===========================================================================

def test_clause_parsed():
    body = "1. Người lao động có quyền.\n2. Người sử dụng lao động có quyền.\n"
    units = parse_content_units(body, "vn:test", 3)
    clause_units = [u for u in units if u["unit_type"] == "clause"]
    assert len(clause_units) == 2
    assert clause_units[0]["clause_number"] == "1"
    assert clause_units[1]["clause_number"] == "2"


def test_year_not_parsed_as_clause():
    """Numbers >= 200 should not be parsed as clause headings."""
    body = "2020. Đây không phải khoản.\n1. Đây mới là khoản.\n"
    units = parse_content_units(body, "vn:test", 4)
    clause_units = [u for u in units if u["unit_type"] == "clause"]
    assert len(clause_units) == 1
    assert clause_units[0]["clause_number"] == "1"


def test_inline_clause_ref_not_parsed():
    """'khoản 1 Điều 5' inside text must not become a clause heading."""
    body = "Theo khoản 1 Điều 5 của Bộ luật Lao động, người lao động có quyền.\n"
    units = parse_content_units(body, "vn:test", 5)
    clause_units = [u for u in units if u["unit_type"] == "clause"]
    assert clause_units == []


# ===========================================================================
# 6. parse_content_units: points
# ===========================================================================

def test_points_parsed():
    body = "1. Khoản một.\na) Điểm a.\nb) Điểm b.\nđ) Điểm đ.\n"
    units = parse_content_units(body, "vn:test", 6)
    point_units = [u for u in units if u["unit_type"] == "point"]
    labels = [u["point_label"] for u in point_units]
    assert "a" in labels
    assert "b" in labels
    assert "đ" in labels


def test_point_clause_number_set():
    body = "1. Khoản một.\na) Điểm a.\n"
    units = parse_content_units(body, "vn:test", 7)
    point = next(u for u in units if u["unit_type"] == "point")
    assert point["clause_number"] == "1"


# ===========================================================================
# 7. stable IDs
# ===========================================================================

def test_stable_article_id_format():
    aid = _stable_article_id("vn:135-2020-nd-cp", 7)
    assert aid == "vn:135-2020-nd-cp:article:7"
    assert "timestamp" not in aid
    assert _stable_article_id("vn:135-2020-nd-cp", 7) == aid


def test_stable_unit_id_preamble():
    uid = _make_unit_id("vn:test", 3, "preamble", 1)
    assert uid == "vbpl:vn:test:article:3|preamble=1"
    assert _make_unit_id("vn:test", 3, "preamble", 1) == uid


def test_stable_unit_id_clause():
    uid = _make_unit_id("vn:test", 3, "clause", 2)
    assert uid == "vbpl:vn:test:article:3|clause=2"


def test_stable_unit_id_point():
    uid = _make_unit_id("vn:test", 3, "point", (2, "a"))
    assert uid == "vbpl:vn:test:article:3|clause=2|point=a"


def test_stable_unit_id():
    assert _make_unit_id("vn:test", 3, "preamble", 1) == "vbpl:vn:test:article:3|preamble=1"
    assert _make_unit_id("vn:test", 3, "clause", 2) == "vbpl:vn:test:article:3|clause=2"
    assert _make_unit_id("vn:test", 3, "point", (2, "a")) == "vbpl:vn:test:article:3|clause=2|point=a"


# ===========================================================================
# 8. No content loss & Unicode
# ===========================================================================

def test_no_content_loss():
    body = "Preamble text.\n1. Khoản một nội dung.\na) Điểm a.\nb) Điểm b.\n2. Khoản hai.\n"
    units = parse_content_units(body, "vn:test", 9)
    combined = " ".join(u["text"] for u in units)
    assert "Preamble text" in combined
    assert "Khoản một" in combined
    assert "Điểm a" in combined
    assert "Điểm b" in combined
    assert "Khoản hai" in combined


def test_unicode_preserved():
    body = "Người lao động Việt Nam được đảm bảo quyền.\n"
    units = parse_content_units(body, "vn:test", 10)
    assert len(units) == 1
    text = units[0]["text"]
    assert "Người lao động" in text
    assert "Việt Nam" in text
    assert "đảm bảo" in text
    assert "quyền" in text


# ===========================================================================
# 9. parse_include_articles
# ===========================================================================

def test_parse_include_range():
    assert parse_include_articles("1-5") == {1, 2, 3, 4, 5}


def test_parse_include_single():
    assert parse_include_articles("7") == {7}


def test_parse_include_comma_list():
    assert parse_include_articles("4,6") == {4, 6}


def test_parse_include_mixed():
    assert parse_include_articles("1-3,7,10-12") == {1, 2, 3, 7, 10, 11, 12}


# ===========================================================================
# 10. build_articles_from_snapshot
# ===========================================================================

def test_fail_missing_article(tmp_path):
    snap = _write_snapshot(tmp_path)
    with pytest.raises(ValueError, match="Missing expected articles"):
        build_articles_from_snapshot(snap, MINIMAL_CONFIG_DOC, {1, 2, 3, 99})


def test_no_duplicate_article_ids(tmp_path):
    snap = _write_snapshot(tmp_path)
    cfg_a = {**MINIMAL_CONFIG_DOC, "canonical_document_id": "vn:doc-a"}
    cfg_b = {**MINIMAL_CONFIG_DOC, "canonical_document_id": "vn:doc-b"}

    arts_a, _, _, _, _ = build_articles_from_snapshot(snap, cfg_a, {1, 2, 3})
    arts_b, _, _, _, _ = build_articles_from_snapshot(snap, cfg_b, {1, 2, 3})

    ids_a = {a["article_id"] for a in arts_a}
    ids_b = {a["article_id"] for a in arts_b}
    assert ids_a.isdisjoint(ids_b)


def test_snapshot_article_schema(tmp_path):
    snap = _write_snapshot(tmp_path)
    articles, validated_cnt, warns, _, _ = build_articles_from_snapshot(snap, MINIMAL_CONFIG_DOC, {1, 2, 3})
    assert len(articles) == 3
    assert validated_cnt == 3
    required_fields = {
        "article_id", "article_code", "article_title", "heading",
        "document_id", "source_document_id", "document_number",
        "source_item_id", "source_urls", "source_sha256",
        "source_adapter", "corpus_role", "content_units",
        "relations", "attachments", "parser_version",
    }
    for art in articles:
        missing = required_fields - art.keys()
        assert not missing, f"Missing fields in article: {missing}"
        assert art["source_document_id"] == "vbpl:item:152734"


def test_snapshot_source_adapter_value(tmp_path):
    snap = _write_snapshot(tmp_path)
    articles, _, _, _, _ = build_articles_from_snapshot(snap, MINIMAL_CONFIG_DOC, {1})
    assert articles[0]["source_adapter"] == "vbpl"
    assert articles[0]["corpus_role"] == "canonical"
    assert articles[0]["source_document_id"] == "vbpl:item:152734"


def test_snapshot_metadata_no_guessing(tmp_path):
    """issued_at, effective_from come from manifest, not guessed."""
    snap = _write_snapshot(tmp_path)
    articles, _, _, _, _ = build_articles_from_snapshot(snap, MINIMAL_CONFIG_DOC, {1})
    assert articles[0]["issued_at"] == "2020-11-18"
    assert articles[0]["effective_from"] == "2021-01-01"
    assert articles[0]["issuing_authority"] == "Chính phủ"


def test_document_number_mismatch_raises(tmp_path):
    bad_manifest = {**MINIMAL_MANIFEST, "document": {
        **MINIMAL_MANIFEST["document"],
        "document_number": "999/2099/NĐ-CP",
    }}
    snap = _write_snapshot(tmp_path, manifest=bad_manifest)
    with pytest.raises(ValueError, match="Document number mismatch"):
        build_articles_from_snapshot(snap, MINIMAL_CONFIG_DOC, {1})


def test_item_id_mismatch_raises(tmp_path):
    bad_manifest = {**MINIMAL_MANIFEST, "document": {
        **MINIMAL_MANIFEST["document"],
        "item_id": "999999",
    }}
    snap = _write_snapshot(tmp_path, manifest=bad_manifest)
    with pytest.raises(ValueError, match="ItemID mismatch"):
        build_articles_from_snapshot(snap, MINIMAL_CONFIG_DOC, {1})


# ===========================================================================
# 11. CLI integration: help, exit codes, output
# ===========================================================================

def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_cli_missing_manifest_exits_2():
    code = main(["--run-manifest", "/nonexistent/run_manifest.json"])
    assert code == 2


def test_cli_full_run(tmp_path):
    snap_dir = tmp_path / "snapshots" / "135_2020_nd_cp" / "20260724T103957Z-abc123"
    snap_dir.mkdir(parents=True)
    (snap_dir / "full_text.txt").write_text(MINIMAL_FULL_TEXT, encoding="utf-8")
    (snap_dir / "manifest.json").write_text(
        json.dumps(MINIMAL_MANIFEST, ensure_ascii=False), encoding="utf-8"
    )

    run_manifest = {
        "run_id": "test-run-001",
        "results": [
            {
                "document_number": "135/2020/NĐ-CP",
                "snapshot_dir": str(snap_dir),
                "status": "skipped_valid",
            }
        ],
    }
    corpus_config = {"documents": [MINIMAL_CONFIG_DOC]}

    manifest_path = tmp_path / "run_manifest.json"
    config_path = tmp_path / "config.json"
    output_path = tmp_path / "out.json"
    report_path = tmp_path / "report.json"

    manifest_path.write_text(json.dumps(run_manifest, ensure_ascii=False), encoding="utf-8")
    config_path.write_text(json.dumps(corpus_config, ensure_ascii=False), encoding="utf-8")

    code = main([
        "--run-manifest", str(manifest_path),
        "--config", str(config_path),
        "--output", str(output_path),
        "--report", str(report_path),
        "--strict",
        "--log-level", "ERROR",
    ])
    assert code == 0
    assert output_path.is_file()

    data = json.loads(output_path.read_text(encoding="utf-8"))
    assert data["metadata"]["articles_selected"] == 3
    assert len(data["articles"]) == 3
    assert data["metadata"]["documents_processed"] == 1


def test_cli_strict_fails_on_missing_article(tmp_path):
    snap_dir = tmp_path / "snap"
    snap_dir.mkdir()
    (snap_dir / "full_text.txt").write_text(MINIMAL_FULL_TEXT, encoding="utf-8")
    (snap_dir / "manifest.json").write_text(
        json.dumps(MINIMAL_MANIFEST, ensure_ascii=False), encoding="utf-8"
    )

    run_manifest = {
        "run_id": "test-strict",
        "results": [{"document_number": "135/2020/NĐ-CP", "snapshot_dir": str(snap_dir), "status": "ok"}],
    }
    cfg_doc = {**MINIMAL_CONFIG_DOC, "expected_articles": 99}
    corpus_config = {"documents": [cfg_doc]}

    manifest_path = tmp_path / "run.json"
    config_path = tmp_path / "cfg.json"
    out = tmp_path / "out.json"
    rep = tmp_path / "rep.json"

    manifest_path.write_text(json.dumps(run_manifest, ensure_ascii=False), encoding="utf-8")
    config_path.write_text(json.dumps(corpus_config, ensure_ascii=False), encoding="utf-8")

    code = main([
        "--run-manifest", str(manifest_path),
        "--config", str(config_path),
        "--output", str(out),
        "--report", str(rep),
        "--strict",
        "--log-level", "ERROR",
    ])
    assert code != 0


# ===========================================================================
# 12. Integration & Regression tests on real corpus
# ===========================================================================

@pytest.mark.integration
def test_integration_full_run():
    """Full integration test verifying validated vs selected counts and scope filters."""
    output = Path("data/processed/vbpl_articles_raw.json")
    report = Path("data/quality/vbpl_article_build_report.json")

    if not output.exists():
        pytest.skip("vbpl_articles_raw.json not present; run build_vbpl_articles.py first")

    data = json.loads(output.read_text(encoding="utf-8"))
    meta = data["metadata"]
    rep = json.loads(report.read_text(encoding="utf-8"))

    assert meta["documents_expected"] == 16
    assert meta["documents_processed"] == 16
    assert meta["articles_validated_total"] == 389
    assert meta["articles_selected"] == 285
    assert meta["articles_excluded_by_scope"] == 104
    assert meta["empty_articles"] == []
    assert rep["status"] == "PASS"
    assert len(data["articles"]) == 285

    for art in data["articles"]:
        item_id = art["source_item_id"]
        assert art["source_document_id"] == f"vbpl:item:{item_id}"

    doc_articles: dict[str, list[int]] = {}
    for art in data["articles"]:
        dnum = art["document_number"]
        doc_articles.setdefault(dnum, []).append(art["article_number"])

    assert doc_articles["152/2020/NĐ-CP"] == list(range(22, 29))  # 7 articles: 22-28
    assert doc_articles["128/2025/NĐ-CP"] == [7]                 # 1 article: 7
    assert doc_articles["129/2025/NĐ-CP"] == list(range(67, 82))  # 15 articles: 67-81


def test_regression_doc_145_article_10():
    """Verify Article 10 of 145/2020/NĐ-CP is the main legal article, not appendix."""
    output = Path("data/processed/vbpl_articles_raw.json")
    if not output.exists():
        pytest.skip("vbpl_articles_raw.json not present")

    data = json.loads(output.read_text(encoding="utf-8"))
    art10 = next(
        (a for a in data["articles"]
         if a.get("document_number") == "145/2020/NĐ-CP" and str(a.get("article_code")) == "10"),
        None
    )

    assert art10 is not None, "Article 10 of 145/2020/NĐ-CP missing in selected output"
    assert art10["article_title"].startswith("Xử lý hợp đồng lao động vô hiệu toàn bộ")
    assert len(art10["content_units"]) > 0

    full_unit_text = "\n".join(u.get("text", "") for u in art10["content_units"])
    assert "Khi hợp đồng lao động bị tuyên bố vô hiệu toàn bộ" in full_unit_text
    assert "Thỏa thuận khác (nếu có)" not in full_unit_text
    assert art10["article_title"] != "Thỏa thuận khác (nếu có)"


def test_regression_doc_145_article_115_appendix_isolation():
    """Verify Article 115 of 145/2020/NĐ-CP does not contain appendix or administrative tail leakage."""
    output = Path("data/processed/vbpl_articles_raw.json")
    if not output.exists():
        pytest.skip("vbpl_articles_raw.json not present")

    data = json.loads(output.read_text(encoding="utf-8"))
    art115 = next(
        (a for a in data["articles"]
         if a.get("document_number") == "145/2020/NĐ-CP" and str(a.get("article_code")) == "115"),
        None
    )

    assert art115 is not None, "Article 115 of 145/2020/NĐ-CP missing"
    assert art115["article_title"] == "Trách nhiệm thi hành"
    assert len(art115["content_units"]) > 0

    full_unit_text = "\n".join(u.get("text", "") for u in art115["content_units"])
    assert "chịu trách nhiệm thi hành Nghị định này" in full_unit_text
    assert "Nơi nhận:" not in full_unit_text
    assert "TM. CHÍNH PHỦ" not in full_unit_text
    assert "Nguyễn Xuân Phúc" not in full_unit_text
    assert "Phụ lục I" not in full_unit_text
    assert "Mẫu số" not in full_unit_text


def test_regression_doc_129_article_81_appendix_isolation():
    """Verify Article 81 of 129/2025/NĐ-CP does not contain appendix or administrative tail leakage."""
    output = Path("data/processed/vbpl_articles_raw.json")
    if not output.exists():
        pytest.skip("vbpl_articles_raw.json not present")

    data = json.loads(output.read_text(encoding="utf-8"))
    art81 = next(
        (a for a in data["articles"]
         if a.get("document_number") == "129/2025/NĐ-CP" and str(a.get("article_code")) == "81"),
        None
    )

    assert art81 is not None, "Article 81 of 129/2025/NĐ-CP missing"
    assert len(art81["content_units"]) > 0

    full_unit_text = "\n".join(u.get("text", "") for u in art81["content_units"])
    assert "chịu trách nhiệm thi hành Nghị định này" in full_unit_text or "1." in full_unit_text
    assert "Nơi nhận:" not in full_unit_text
    assert "TM. CHÍNH PHỦ" not in full_unit_text
    assert "KT. THỦ TƯỚNG" not in full_unit_text
    assert "Nguyễn Hòa Bình" not in full_unit_text
    assert "PHỤ LỤC I" not in full_unit_text
    assert "Mẫu số" not in full_unit_text


def test_fixture_appendix_in_normal_sentence_not_truncated():
    """Text with 'phụ lục' in normal sentence is not truncated."""
    text = (
        "Điều 1. Phạm vi điều chỉnh\n"
        "Hồ sơ, thủ tục công nhận được quy định tại Phụ lục I ban hành kèm theo Nghị định này.\n"
        "1. Khoản 1 nội dung bình thường.\n"
    )
    headings = find_article_headings(text)
    seq = select_main_sequence(headings, {1})
    start_off = seq[0][2]
    heading_line_end = text.find("\n", start_off) + 1

    app_info = find_appendix_info(text, heading_line_end)
    assert app_info is None
    body = text[heading_line_end:]
    assert "Phụ lục I ban hành" in body


def test_fixture_standalone_appendix_heading_truncated():
    """Text with standalone 'Phụ lục I' heading at start of line is truncated."""
    text = (
        "Điều 1. Điều cuối\n"
        "1. Trách nhiệm thi hành.\n"
        "Phụ lục I\n"
        "Biểu mẫu đính kèm.\n"
    )
    headings = find_article_headings(text)
    seq = select_main_sequence(headings, {1})
    start_off = seq[0][2]
    heading_line_end = text.find("\n", start_off) + 1

    app_info = find_appendix_info(text, heading_line_end)
    assert app_info is not None
    app_off, app_title = app_info
    assert app_title == "Phụ lục I"
    body = text[heading_line_end:app_off]
    assert "Trách nhiệm thi hành" in body
    assert "Phụ lục I" not in body


def test_fixture_administrative_tail_in_normal_sentence_not_truncated():
    """Sentence containing 'nơi nhận hồ sơ' is not truncated."""
    text = (
        "Điều 1. Phạm vi điều chỉnh\n"
        "Địa chỉ này là nơi nhận hồ sơ đề nghị công nhận của doanh nghiệp.\n"
        "1. Khoản 1 nội dung bình thường.\n"
    )
    headings = find_article_headings(text)
    seq = select_main_sequence(headings, {1})
    start_off = seq[0][2]
    heading_line_end = text.find("\n", start_off) + 1

    admin_info = find_administrative_tail_info(text, heading_line_end)
    assert admin_info is None
    body = text[heading_line_end:]
    assert "nơi nhận hồ sơ" in body


def test_fixture_standalone_administrative_tail_heading_truncated():
    """Text with standalone 'Nơi nhận:' heading at start of line is truncated."""
    text = (
        "Điều 1. Điều cuối\n"
        "1. Trách nhiệm thi hành.\n"
        "Nơi nhận:\n"
        "- Như trên;\n"
    )
    headings = find_article_headings(text)
    seq = select_main_sequence(headings, {1})
    start_off = seq[0][2]
    heading_line_end = text.find("\n", start_off) + 1

    admin_info = find_administrative_tail_info(text, heading_line_end)
    assert admin_info is not None
    admin_off, admin_title = admin_info
    assert admin_title == "Nơi nhận:"
    body = text[heading_line_end:admin_off]
    assert "Trách nhiệm thi hành" in body
    assert "Nơi nhận:" not in body


def test_report_leakage_articles_empty():
    """Verify appendix_leakage_articles and administrative_tail_leakage_articles are empty."""
    report_path = Path("data/quality/vbpl_article_build_report.json")
    if not report_path.exists():
        pytest.skip("vbpl_article_build_report.json not present")

    rep = json.loads(report_path.read_text(encoding="utf-8"))
    assert rep.get("appendix_leakage_articles") == []
    assert rep.get("administrative_tail_leakage_articles") == []
