"""Unit and integration tests for scripts/build_vbpl_articles.py.

Run:
    pytest backend/tests/test_vbpl_article_adapter.py -v
    pytest backend/tests/test_vbpl_article_adapter.py -v -m integration
"""
from __future__ import annotations

import json
import sys
import tempfile
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
    build_articles_from_snapshot,
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
    snap.mkdir(parents=True)
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
    # Only the heading at start-of-line is captured
    assert len(headings) == 1
    assert headings[0][0] == 5


# ===========================================================================
# 3. select_main_sequence: correct contiguous run
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
    # Offsets should be from the first occurrence
    assert seq[0][2] == 0
    assert seq[1][2] == 50


def test_select_main_sequence_subset():
    headings = [(1, "A", 0), (2, "B", 10), (3, "C", 20), (4, "D", 30)]
    seq = select_main_sequence(headings, {2, 3})
    assert [h[0] for h in seq] == [2, 3]


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
    # No clause should be parsed — the "1" is not at line start
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
# 7. stable article_id (no timestamp)
# ===========================================================================

def test_stable_article_id_format():
    aid = _stable_article_id("vn:135-2020-nd-cp", 7)
    assert aid == "vn:135-2020-nd-cp:article:7"
    assert "timestamp" not in aid
    # Same call must yield same result
    assert _stable_article_id("vn:135-2020-nd-cp", 7) == aid


# ===========================================================================
# 8. stable unit_id
# ===========================================================================

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


# ===========================================================================
# 9. No content loss when joining units
# ===========================================================================

def test_no_content_loss():
    body = "Preamble text.\n1. Khoản một nội dung.\na) Điểm a.\nb) Điểm b.\n2. Khoản hai.\n"
    units = parse_content_units(body, "vn:test", 9)
    combined = " ".join(u["text"] for u in units)
    # Key phrases must all be present
    assert "Preamble text" in combined
    assert "Khoản một" in combined
    assert "Điểm a" in combined
    assert "Điểm b" in combined
    assert "Khoản hai" in combined


# ===========================================================================
# 10. Unicode preserved
# ===========================================================================

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
# 11. Fail when article missing
# ===========================================================================

def test_fail_missing_article(tmp_path):
    """Should raise ValueError when include_set requests an article not in text."""
    snap = _write_snapshot(tmp_path)
    include_set = {1, 2, 3, 99}  # article 99 does not exist
    with pytest.raises(ValueError, match="Missing articles"):
        build_articles_from_snapshot(snap, MINIMAL_CONFIG_DOC, include_set)


# ===========================================================================
# 12. No duplicate article_ids across documents
# ===========================================================================

def test_no_duplicate_article_ids(tmp_path):
    """Two documents must not produce the same article_id."""
    snap = _write_snapshot(tmp_path)
    cfg_a = {**MINIMAL_CONFIG_DOC, "canonical_document_id": "vn:doc-a"}
    cfg_b = {**MINIMAL_CONFIG_DOC, "canonical_document_id": "vn:doc-b"}

    arts_a, _ = build_articles_from_snapshot(snap, cfg_a, {1, 2, 3})
    arts_b, _ = build_articles_from_snapshot(snap, cfg_b, {1, 2, 3})

    ids_a = {a["article_id"] for a in arts_a}
    ids_b = {a["article_id"] for a in arts_b}
    assert ids_a.isdisjoint(ids_b), "Duplicate IDs across documents"


# ===========================================================================
# 13. parse_include_articles
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
# 14. build_articles_from_snapshot: schema fields
# ===========================================================================

def test_snapshot_article_schema(tmp_path):
    snap = _write_snapshot(tmp_path)
    articles, warns = build_articles_from_snapshot(snap, MINIMAL_CONFIG_DOC, {1, 2, 3})
    assert len(articles) == 3
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


def test_snapshot_source_adapter_value(tmp_path):
    snap = _write_snapshot(tmp_path)
    articles, _ = build_articles_from_snapshot(snap, MINIMAL_CONFIG_DOC, {1})
    assert articles[0]["source_adapter"] == "vbpl"
    assert articles[0]["corpus_role"] == "canonical"


def test_snapshot_metadata_no_guessing(tmp_path):
    """issued_at, effective_from come from manifest, not guessed."""
    snap = _write_snapshot(tmp_path)
    articles, _ = build_articles_from_snapshot(snap, MINIMAL_CONFIG_DOC, {1})
    assert articles[0]["issued_at"] == "2020-11-18"
    assert articles[0]["effective_from"] == "2021-01-01"
    assert articles[0]["issuing_authority"] == "Chính phủ"


# ===========================================================================
# 15. Document-number / ItemID mismatch causes error
# ===========================================================================

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
# 16. CLI integration: help, exit codes, output
# ===========================================================================

def test_cli_help_exits_zero():
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_cli_missing_manifest_exits_2():
    code = main(["--run-manifest", "/nonexistent/run_manifest.json"])
    assert code == 2


def test_cli_full_run(tmp_path):
    """Full CLI run against synthetic snapshot."""
    # Build synthetic snapshot tree
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
    corpus_config = {
        "documents": [MINIMAL_CONFIG_DOC],
    }

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
    assert data["metadata"]["articles_created"] == 3
    assert len(data["articles"]) == 3
    assert data["metadata"]["documents_processed"] == 1
    assert data["metadata"]["missing_documents"] == []
    assert data["metadata"]["duplicate_article_ids"] == []


def test_cli_strict_fails_on_missing_article(tmp_path):
    """--strict must return non-zero when article count mismatches."""
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
    # Config claims 99 articles, but only 3 exist
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
# Integration test (requires runtime data)
# ===========================================================================

@pytest.mark.integration
def test_integration_full_run():
    """Full integration test against real VBPL snapshots."""
    output = Path("data/processed/vbpl_articles_raw.json")
    report = Path("data/quality/vbpl_article_build_report.json")

    if not output.exists():
        pytest.skip("vbpl_articles_raw.json not present; run build_vbpl_articles.py first")

    data = json.loads(output.read_text(encoding="utf-8"))
    meta = data["metadata"]

    # Count expected from config (source of truth)
    config = json.loads(Path("config/vbpl_corpus.json").read_text(encoding="utf-8"))
    manifest = json.loads(Path("data/raw/vbpl/run_manifest.json").read_text(encoding="utf-8"))
    manifest_docs = {r["document_number"] for r in manifest["results"]}
    expected_total = sum(
        d.get("expected_articles", 0)
        for d in config["documents"]
        if d["document_number"] in manifest_docs
    )

    assert meta["documents_expected"] == 16
    assert meta["documents_processed"] == 16
    assert meta["articles_expected"] == expected_total
    assert meta["articles_created"] == expected_total
    assert meta["missing_documents"] == []
    assert meta["duplicate_article_ids"] == []
    assert meta["empty_articles"] == []
    assert len(data["articles"]) == expected_total

    # Check no duplicate IDs
    ids = [a["article_id"] for a in data["articles"]]
    assert len(ids) == len(set(ids))

    # Validate each article has required fields
    required = {
        "article_id", "article_code", "article_title", "heading",
        "document_id", "source_document_id", "document_number",
        "source_urls", "source_sha256", "source_adapter",
        "corpus_role", "content_units", "relations", "attachments",
        "parser_version",
    }
    for art in data["articles"]:
        assert not (required - art.keys()), f"Missing fields: {required - art.keys()}"
        assert art["source_adapter"] == "vbpl"
        assert art["corpus_role"] == "canonical"

    # JSON serializable
    json.dumps(data)  # should not raise
