import json

import pytest

from app.services.official_sources import (
    OfficialSourceRecord,
    OfficialSourceRegistry,
)
from app.services.source_catalog import (
    ArticleNotFoundError,
    LegalSourceCatalog,
)


def _chunk(
    *,
    chunk_id: str,
    clause: str,
    body: str,
) -> dict:
    return {
        "chunk_id": chunk_id,
        "article_code": "20.2.LQ.169",
        "article_title": "Tuổi nghỉ hưu",
        "article_number": "169",
        "chunk_type": "clause",
        "unit_type": "clause",
        "clause_number": clause,
        "point_labels": [],
        "body_text": body,
        "source_type": "LQ",
        "source_document_id": "vbpl:item:139264",
        "source_note_text": "Điều 169 Bộ luật số 45/2019/QH14",
        "source_urls": [
            "http://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=139264#Dieu_169"
        ],
        "topic_code": "20.2",
        "topic_name": "Lao động",
        "chapter_number": "XII",
        "chapter_title": "BẢO HIỂM XÃ HỘI",
    }


@pytest.fixture
def source_catalog(tmp_path):
    chunks = [
        _chunk(chunk_id="chunk-1", clause="1", body="1. Nội dung khoản một."),
        _chunk(chunk_id="chunk-2", clause="2", body="2. Nội dung khoản hai."),
    ]
    chunks_path = tmp_path / "legal_chunks.jsonl"
    chunks_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in chunks),
        encoding="utf-8",
    )
    registry = OfficialSourceRegistry([
        OfficialSourceRecord(
            source_document_id="vbpl:item:139264",
            item_id="139264",
            document_number="45/2019/QH14",
            title="Bộ luật Lao động",
            canonical_url="https://vanban.chinhphu.vn/?docid=198540&pageid=27160",
            original_url="https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=139264",
            last_checked_at="2026-07-22",
            url_status="active",
        )
    ])
    return LegalSourceCatalog(
        chunks_path=chunks_path,
        official_sources=registry,
    )


def test_catalog_returns_complete_article_in_corpus_order(source_catalog):
    article = source_catalog.get_article("20.2.LQ.169")

    assert article.article_code == "20.2.LQ.169"
    assert article.article_title == "Tuổi nghỉ hưu"
    assert article.document_number == "45/2019/QH14"
    assert article.chunk_count == 2
    assert [unit.label for unit in article.units] == ["Khoản 1", "Khoản 2"]
    assert [unit.content for unit in article.units] == [
        "1. Nội dung khoản một.",
        "2. Nội dung khoản hai.",
    ]
    assert article.official_url.endswith("docid=198540&pageid=27160")
    assert article.url_last_checked_at == "2026-07-22"


def test_catalog_lookup_is_case_insensitive(source_catalog):
    assert source_catalog.get_article("20.2.lq.169").article_code == "20.2.LQ.169"


def test_catalog_rejects_invalid_or_missing_article_code(source_catalog):
    with pytest.raises(ValueError, match="không hợp lệ"):
        source_catalog.get_article("../20.2.LQ.169")
    with pytest.raises(ArticleNotFoundError):
        source_catalog.get_article("20.2.LQ.999")
