import json
from pathlib import Path
import pytest

from app.core.config import settings
from app.services.official_sources import (
    OfficialSourceRecord,
    OfficialSourceRegistry,
)
from app.services.source_catalog import (
    DocumentNotFoundError,
    LegalSourceCatalog,
    SourceCatalogError,
    get_source_catalog,
)


def _make_chunk(
    *,
    chunk_id: str,
    doc_id: str = "vn:bll-2019",
    article_code: str = "20.2.LQ.169",
    article_number: str = "169",
    article_title: str = "Tuổi nghỉ hưu",
    clause: str = "1",
    body: str = "Nội dung khoản 1.",
    source_type: str = "LQ",
    doc_number: str = "45/2019/QH14",
    source_urls: list[str] | None = None,
) -> dict:
    urls = source_urls if source_urls is not None else ["https://vanban.chinhphu.vn/?docid=198540"]
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "article_code": article_code,
        "article_title": article_title,
        "article_number": article_number,
        "chunk_type": "clause",
        "unit_type": "clause",
        "clause_number": clause,
        "point_labels": [],
        "body_text": body,
        "source_type": source_type,
        "source_document_id": "vbpl:item:139264",
        "document_number": doc_number,
        "source_note_text": f"Điều {article_number} Bộ luật số {doc_number}",
        "source_urls": urls,
        "topic_code": "20.2",
        "topic_name": "Lao động",
        "chapter_number": "XII",
        "chapter_title": "BẢO HIỂM XÃ HỘI",
    }


def _make_articles_json(
    *,
    release_id: str = "test-release-2026",
    law_as_of: str = "2026-07-27",
    articles: list[dict] | None = None,
) -> dict:
    if articles is None:
        articles = [
            {
                "document_id": "vn:bll-2019",
                "article_code": "20.2.LQ.169",
                "article_number": 169,
                "document_title": "Bộ luật Lao động",
                "issuing_authority": "Quốc hội",
                "issued_at": "2019-11-20",
                "effective_from": "2021-01-01",
                "legal_status": "in_force",
            },
            {
                "document_id": "vn:bll-2019",
                "article_code": "20.2.LQ.170",
                "article_number": 170,
                "document_title": "Bộ luật Lao động",
                "issuing_authority": "Quốc hội",
                "issued_at": "2019-11-20",
                "effective_from": "2021-01-01",
                "legal_status": "in_force",
            },
            {
                "document_id": "vn:nd-145",
                "article_code": "20.2.ND.15",
                "article_number": 15,
                "document_title": "Nghị định quy định chi tiết Bộ luật Lao động",
                "issuing_authority": "Chính phủ",
                "issued_at": "2020-12-14",
                "effective_from": "2021-02-01",
                "legal_status": "in_force",
            },
        ]
    return {
        "metadata": {
            "release_id": release_id,
            "law_as_of": law_as_of,
        },
        "articles": articles,
    }


@pytest.fixture
def catalog_fixture(tmp_path):
    chunks = [
        _make_chunk(chunk_id="chunk-169-1", doc_id="vn:bll-2019", article_code="20.2.LQ.169", article_number="169", clause="1", body="Khoản 1 Điều 169."),
        _make_chunk(chunk_id="chunk-169-2", doc_id="vn:bll-2019", article_code="20.2.LQ.169", article_number="169", clause="2", body="Khoản 2 Điều 169."),
        _make_chunk(chunk_id="chunk-170-1", doc_id="vn:bll-2019", article_code="20.2.LQ.170", article_number="170", article_title="Bảo hiểm xã hội", clause="1", body="Khoản 1 Điều 170."),
        _make_chunk(chunk_id="chunk-nd15-1", doc_id="vn:nd-145", article_code="20.2.ND.15", article_number="15", article_title="Ký quỹ hoạt động", source_type="NĐ", doc_number="145/2020/NĐ-CP", clause="1", body="Khoản 1 Điều 15."),
    ]
    chunks_file = tmp_path / "canonical_chunks.jsonl"
    chunks_file.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks),
        encoding="utf-8",
    )

    articles_data = _make_articles_json()
    articles_file = tmp_path / "canonical_articles.json"
    articles_file.write_text(
        json.dumps(articles_data, ensure_ascii=False),
        encoding="utf-8",
    )

    registry = OfficialSourceRegistry([
        OfficialSourceRecord(
            source_document_id="vbpl:item:139264",
            item_id="139264",
            document_number="45/2019/QH14",
            title="Bộ luật Lao động",
            canonical_url="https://vanban.chinhphu.vn/?docid=198540",
            original_url="https://vbpl.vn/TW/Pages/vbpq-toanvan.aspx?ItemID=139264",
            last_checked_at="2026-07-22",
            url_status="active",
        )
    ])

    return LegalSourceCatalog(
        chunks_path=chunks_file,
        official_sources=registry,
        canonical_articles_path=articles_file,
    )


# ----------------------------------------------------------------------
# A. SERVICE CATALOG BEHAVIOR TESTS
# ----------------------------------------------------------------------

def test_catalog_without_canonical_articles_path_supports_get_article(tmp_path):
    chunks = [_make_chunk(chunk_id="c1")]
    chunks_file = tmp_path / "canonical_chunks.jsonl"
    chunks_file.write_text(json.dumps(chunks[0]), encoding="utf-8")

    cat = LegalSourceCatalog(
        chunks_path=chunks_file,
        official_sources=OfficialSourceRegistry([]),
    )
    art = cat.get_article("20.2.LQ.169")
    assert art.article_code == "20.2.LQ.169"


def test_catalog_groups_multiple_chunks_same_article_code(catalog_fixture):
    art = catalog_fixture.get_article("20.2.LQ.169")
    assert art.article_code == "20.2.LQ.169"
    assert art.chunk_count == 2
    assert len(art.units) == 2


def test_article_count_counts_unique_articles_not_chunks(catalog_fixture):
    doc = catalog_fixture.get_document("vn:bll-2019")
    # vn:bll-2019 has 3 chunks total but only 2 unique articles (169 and 170)
    assert doc.article_count == 2


def test_articles_sorted_by_article_number_then_article_code(catalog_fixture):
    res = catalog_fixture.list_document_articles("vn:bll-2019")
    codes = [a.article_code for a in res.articles]
    assert codes == ["20.2.LQ.169", "20.2.LQ.170"]


def test_articles_sorted_by_article_number_tie_breaker_uses_article_code(tmp_path):
    # Two articles with identical article_number (169), but codes '20.2.LQ.169.B' and '20.2.LQ.169.A'
    chunks = [
        _make_chunk(chunk_id="c-b", doc_id="vn:bll-2019", article_code="20.2.LQ.169.B", article_number="169"),
        _make_chunk(chunk_id="c-a", doc_id="vn:bll-2019", article_code="20.2.LQ.169.A", article_number="169"),
    ]
    chunks_file = tmp_path / "canonical_chunks.jsonl"
    chunks_file.write_text("\n".join(json.dumps(c) for c in chunks), encoding="utf-8")

    articles_data = _make_articles_json(articles=[
        {"document_id": "vn:bll-2019", "article_code": "20.2.LQ.169.B", "article_number": 169},
        {"document_id": "vn:bll-2019", "article_code": "20.2.LQ.169.A", "article_number": 169},
    ])
    articles_file = tmp_path / "canonical_articles.json"
    articles_file.write_text(json.dumps(articles_data), encoding="utf-8")

    cat = LegalSourceCatalog(
        chunks_path=chunks_file,
        official_sources=OfficialSourceRegistry([]),
        canonical_articles_path=articles_file,
    )
    res = cat.list_document_articles("vn:bll-2019")
    codes = [a.article_code for a in res.articles]
    assert codes == ["20.2.LQ.169.A", "20.2.LQ.169.B"]


def test_document_list_has_stable_order(catalog_fixture):
    res = catalog_fixture.list_documents()
    # LQ sorted before NĐ
    types = [d.source_type for d in res.documents]
    assert types == ["LQ", "NĐ"]


def test_search_document_by_document_number(catalog_fixture):
    res = catalog_fixture.list_documents(q="145/2020")
    assert res.pagination.total == 1
    assert res.documents[0].document_id == "vn:nd-145"


def test_search_document_by_vietnamese_accented_title(catalog_fixture):
    res = catalog_fixture.list_documents(q="Bộ luật Lao động")
    # Both documents contain 'Bộ luật Lao động' in title
    assert res.pagination.total == 2
    assert res.documents[0].document_id == "vn:bll-2019"


def test_search_document_by_vietnamese_unaccented_title(catalog_fixture):
    res = catalog_fixture.list_documents(q="bo luat lao dong")
    assert res.pagination.total == 2
    assert res.documents[0].document_id == "vn:bll-2019"


def test_search_document_case_insensitive(catalog_fixture):
    res = catalog_fixture.list_documents(q="bO lUaT")
    assert res.pagination.total == 2
    assert res.documents[0].document_id == "vn:bll-2019"


def test_search_document_whitespace_only_query(catalog_fixture):
    res = catalog_fixture.list_documents(q="   ")
    assert res.pagination.total == 2


def test_filter_by_document_type(catalog_fixture):
    res = catalog_fixture.list_documents(document_type="  NĐ  ")
    assert res.pagination.total == 1
    assert res.documents[0].document_id == "vn:nd-145"

    res_empty = catalog_fixture.list_documents(document_type="   ")
    assert res_empty.pagination.total == 2


def test_filter_by_document_type_unaccented_case_insensitive(catalog_fixture):
    res = catalog_fixture.list_documents(document_type="nđ")
    assert res.pagination.total == 1
    assert res.documents[0].document_id == "vn:nd-145"
    assert res.documents[0].source_type == "NĐ"


def test_search_article_by_article_number(catalog_fixture):
    res = catalog_fixture.list_document_articles("vn:bll-2019", q="170")
    assert res.pagination.total == 1
    assert res.articles[0].article_code == "20.2.LQ.170"


def test_search_article_by_heading_title(catalog_fixture):
    res = catalog_fixture.list_document_articles("vn:bll-2019", q="Tuổi nghỉ hưu")
    assert res.pagination.total == 1
    assert res.articles[0].article_code == "20.2.LQ.169"


def test_search_article_unaccented(catalog_fixture):
    res = catalog_fixture.list_document_articles("vn:bll-2019", q="tuoi nghi huu")
    assert res.pagination.total == 1
    assert res.articles[0].article_code == "20.2.LQ.169"


def test_pagination_documents(catalog_fixture):
    # Page 1 with page_size=1
    p1 = catalog_fixture.list_documents(page=1, page_size=1)
    assert p1.pagination.total == 2
    assert p1.pagination.total_pages == 2
    assert len(p1.documents) == 1

    # Page 2
    p2 = catalog_fixture.list_documents(page=2, page_size=1)
    assert len(p2.documents) == 1

    # Page beyond bounds
    p_over = catalog_fixture.list_documents(page=99, page_size=10)
    assert len(p_over.documents) == 0
    assert p_over.pagination.page == 99

    # Empty search
    p_empty = catalog_fixture.list_documents(q="nonexistent_xyz")
    assert p_empty.pagination.total == 0
    assert p_empty.pagination.total_pages == 0


def test_pagination_articles(catalog_fixture):
    p1 = catalog_fixture.list_document_articles("vn:bll-2019", page=1, page_size=1)
    assert p1.pagination.total == 2
    assert p1.pagination.total_pages == 2
    assert len(p1.articles) == 1

    p_over = catalog_fixture.list_document_articles("vn:bll-2019", page=99, page_size=10)
    assert len(p_over.articles) == 0

    p_empty = catalog_fixture.list_document_articles("vn:bll-2019", q="nonexistent_xyz")
    assert p_empty.pagination.total == 0
    assert p_empty.pagination.total_pages == 0


def test_get_document_nonexistent_raises_document_not_found_error(catalog_fixture):
    with pytest.raises(DocumentNotFoundError):
        catalog_fixture.get_document("vn:nonexistent")


def test_official_url_canonical_url_preferred(catalog_fixture):
    doc = catalog_fixture.get_document("vn:bll-2019")
    assert doc.official_url == "https://vanban.chinhphu.vn/?docid=198540"


def test_official_url_fallback_valid_source_url(tmp_path):
    chunk = _make_chunk(
        chunk_id="c1",
        doc_id="vn:doc-fallback",
        article_code="20.2.LQ.1",
        source_urls=["https://example.gov.vn/van-ban/1"]
    )
    chunks_file = tmp_path / "canonical_chunks.jsonl"
    chunks_file.write_text(json.dumps(chunk), encoding="utf-8")

    articles_data = _make_articles_json(articles=[{
        "document_id": "vn:doc-fallback",
        "article_code": "20.2.LQ.1",
        "article_number": 1,
    }])
    articles_file = tmp_path / "canonical_articles.json"
    articles_file.write_text(json.dumps(articles_data), encoding="utf-8")

    cat = LegalSourceCatalog(
        chunks_path=chunks_file,
        official_sources=OfficialSourceRegistry([]),
        canonical_articles_path=articles_file,
    )
    doc = cat.get_document("vn:doc-fallback")
    assert doc.official_url == "https://example.gov.vn/van-ban/1"


@pytest.mark.parametrize("bad_url", [
    "file:///etc/passwd",
    "/local/path",
    "http:///nohost",
    "ftp://example.com/file",
])
def test_official_url_filters_invalid_urls(tmp_path, bad_url):
    chunk = _make_chunk(
        chunk_id="c1",
        doc_id="vn:doc-badurl",
        article_code="20.2.LQ.1",
        source_urls=[bad_url]
    )
    chunks_file = tmp_path / f"chunks_{abs(hash(bad_url))}.jsonl"
    chunks_file.write_text(json.dumps(chunk), encoding="utf-8")

    articles_data = _make_articles_json(articles=[{
        "document_id": "vn:doc-badurl",
        "article_code": "20.2.LQ.1",
        "article_number": 1,
    }])
    articles_file = tmp_path / f"articles_{abs(hash(bad_url))}.json"
    articles_file.write_text(json.dumps(articles_data), encoding="utf-8")

    cat = LegalSourceCatalog(
        chunks_path=chunks_file,
        official_sources=OfficialSourceRegistry([]),
        canonical_articles_path=articles_file,
    )
    doc = cat.get_document("vn:doc-badurl")
    assert doc.official_url is None


def test_official_url_none_if_no_valid_url(tmp_path):
    chunk = _make_chunk(chunk_id="c1", doc_id="vn:doc-nourl", article_code="20.2.LQ.1", source_urls=[])
    chunks_file = tmp_path / "chunks_nourl.jsonl"
    chunks_file.write_text(json.dumps(chunk), encoding="utf-8")

    articles_data = _make_articles_json(articles=[{
        "document_id": "vn:doc-nourl",
        "article_code": "20.2.LQ.1",
        "article_number": 1,
    }])
    articles_file = tmp_path / "articles_nourl.json"
    articles_file.write_text(json.dumps(articles_data), encoding="utf-8")

    cat = LegalSourceCatalog(
        chunks_path=chunks_file,
        official_sources=OfficialSourceRegistry([]),
        canonical_articles_path=articles_file,
    )
    doc = cat.get_document("vn:doc-nourl")
    assert doc.official_url is None


def test_source_adapter_returned_and_no_source_provider(catalog_fixture):
    doc = catalog_fixture.get_document("vn:bll-2019")
    assert hasattr(doc, "source_adapter")
    assert not hasattr(doc, "source_provider")


def test_law_as_of_passed_to_list_and_detail(catalog_fixture):
    l_res = catalog_fixture.list_documents()
    assert l_res.law_as_of == "2026-07-27"

    doc = catalog_fixture.get_document("vn:bll-2019")
    assert doc.law_as_of == "2026-07-27"


def test_release_id_prioritizes_artifact_metadata(catalog_fixture):
    l_res = catalog_fixture.list_documents()
    assert l_res.release_id == "test-release-2026"


def test_release_id_fallback_when_artifact_lacks_release_id(tmp_path):
    chunk = _make_chunk(chunk_id="c1", doc_id="vn:doc-no-rel", article_code="20.2.LQ.1")
    chunks_file = tmp_path / "chunks_norel.jsonl"
    chunks_file.write_text(json.dumps(chunk), encoding="utf-8")

    articles_data = {
        "metadata": {"law_as_of": "2026-01-01"},  # No release_id field
        "articles": [{
            "document_id": "vn:doc-no-rel",
            "article_code": "20.2.LQ.1",
            "article_number": 1,
        }]
    }
    articles_file = tmp_path / "articles_norel.json"
    articles_file.write_text(json.dumps(articles_data), encoding="utf-8")

    cat = LegalSourceCatalog(
        chunks_path=chunks_file,
        official_sources=OfficialSourceRegistry([]),
        canonical_articles_path=articles_file,
    )
    l_res = cat.list_documents()
    assert l_res.release_id == settings.corpus_release_id


# ----------------------------------------------------------------------
# B. FAIL-CLOSED CANONICAL_ARTICLES.JSON TESTS
# ----------------------------------------------------------------------

def _run_fail_closed_test(tmp_path, articles_raw, chunks_list=None):
    if chunks_list is None:
        chunks_list = [_make_chunk(chunk_id="c1")]

    chunks_file = tmp_path / "canonical_chunks.jsonl"
    chunks_file.write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks_list),
        encoding="utf-8",
    )

    articles_file = tmp_path / "canonical_articles.json"
    if isinstance(articles_raw, str):
        articles_file.write_text(articles_raw, encoding="utf-8")
    elif articles_raw is not None:
        articles_file.write_text(json.dumps(articles_raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SourceCatalogError):
        LegalSourceCatalog(
            chunks_path=chunks_file,
            official_sources=OfficialSourceRegistry([]),
            canonical_articles_path=articles_file if articles_raw is not None else tmp_path / "nonexistent.json",
        )


def test_fail_closed_missing_file(tmp_path):
    _run_fail_closed_test(tmp_path, articles_raw=None)


def test_fail_closed_unreadable_file_oserror(tmp_path, monkeypatch):
    chunks_file = tmp_path / "canonical_chunks.jsonl"
    chunks_file.write_text(json.dumps(_make_chunk(chunk_id="c1")), encoding="utf-8")

    articles_file = tmp_path / "canonical_articles.json"
    articles_file.write_text(json.dumps(_make_articles_json()), encoding="utf-8")

    orig_read_text = Path.read_text

    def mock_read_text(self, *args, **kwargs):
        if self.name == "canonical_articles.json":
            raise OSError("I/O error reading canonical_articles.json")
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", mock_read_text)

    with pytest.raises(SourceCatalogError, match="Không thể đọc"):
        LegalSourceCatalog(
            chunks_path=chunks_file,
            official_sources=OfficialSourceRegistry([]),
            canonical_articles_path=articles_file,
        )


def test_fail_closed_invalid_json(tmp_path):
    _run_fail_closed_test(tmp_path, articles_raw="invalid json {{{")


def test_fail_closed_root_not_object(tmp_path):
    _run_fail_closed_test(tmp_path, articles_raw=[1, 2, 3])


def test_fail_closed_metadata_not_object(tmp_path):
    _run_fail_closed_test(tmp_path, articles_raw={"metadata": "not_an_obj", "articles": []})


def test_fail_closed_articles_not_list(tmp_path):
    _run_fail_closed_test(tmp_path, articles_raw={"metadata": {}, "articles": "not_a_list"})


def test_fail_closed_articles_empty(tmp_path):
    _run_fail_closed_test(tmp_path, articles_raw={"metadata": {}, "articles": []})


def test_fail_closed_article_element_not_object(tmp_path):
    _run_fail_closed_test(tmp_path, articles_raw={"metadata": {}, "articles": ["not_an_obj"]})


def test_fail_closed_missing_document_id(tmp_path):
    _run_fail_closed_test(tmp_path, articles_raw={
        "metadata": {},
        "articles": [{"article_code": "20.2.LQ.169"}]
    })


def test_fail_closed_missing_article_code(tmp_path):
    _run_fail_closed_test(tmp_path, articles_raw={
        "metadata": {},
        "articles": [{"document_id": "vn:bll-2019"}]
    })


def test_fail_closed_duplicate_article_code_same_doc(tmp_path):
    _run_fail_closed_test(tmp_path, articles_raw={
        "metadata": {},
        "articles": [
            {"document_id": "vn:bll-2019", "article_code": "20.2.LQ.169"},
            {"document_id": "vn:bll-2019", "article_code": "20.2.LQ.169"},
        ]
    })


def test_fail_closed_duplicate_article_code_different_doc(tmp_path):
    _run_fail_closed_test(tmp_path, articles_raw={
        "metadata": {},
        "articles": [
            {"document_id": "vn:bll-2019", "article_code": "20.2.LQ.169"},
            {"document_id": "vn:nd-145", "article_code": "20.2.LQ.169"},
        ]
    })


def test_fail_closed_article_code_set_mismatch(tmp_path):
    # JSONL has 20.2.LQ.169, articles.json has 20.2.LQ.999
    _run_fail_closed_test(
        tmp_path,
        articles_raw=_make_articles_json(articles=[{
            "document_id": "vn:bll-2019",
            "article_code": "20.2.LQ.999",
            "article_number": 999
        }]),
        chunks_list=[_make_chunk(chunk_id="c1", article_code="20.2.LQ.169")]
    )


def test_fail_closed_article_code_doc_id_mismatch(tmp_path):
    # JSONL says doc_id=vn:bll-2019, articles.json says doc_id=vn:nd-145 for 20.2.LQ.169
    _run_fail_closed_test(
        tmp_path,
        articles_raw=_make_articles_json(articles=[{
            "document_id": "vn:nd-145",
            "article_code": "20.2.LQ.169",
            "article_number": 169
        }]),
        chunks_list=[_make_chunk(chunk_id="c1", doc_id="vn:bll-2019", article_code="20.2.LQ.169")]
    )


def test_fail_closed_chunk_missing_document_id(tmp_path):
    chunk = _make_chunk(chunk_id="c1", doc_id="", article_code="20.2.LQ.169")
    del chunk["document_id"]
    _run_fail_closed_test(
        tmp_path,
        articles_raw=_make_articles_json(articles=[{
            "document_id": "vn:bll-2019",
            "article_code": "20.2.LQ.169",
            "article_number": 169
        }]),
        chunks_list=[chunk]
    )


def test_fail_closed_inconsistent_chunk_doc_ids_same_code(tmp_path):
    # 2 chunks for same article_code 20.2.LQ.169 but different document_ids
    c1 = _make_chunk(chunk_id="c1", doc_id="vn:bll-2019", article_code="20.2.LQ.169")
    c2 = _make_chunk(chunk_id="c2", doc_id="vn:nd-145", article_code="20.2.LQ.169")
    _run_fail_closed_test(
        tmp_path,
        articles_raw=_make_articles_json(articles=[{
            "document_id": "vn:bll-2019",
            "article_code": "20.2.LQ.169",
            "article_number": 169
        }]),
        chunks_list=[c1, c2]
    )


def test_fail_closed_different_release_directories(tmp_path):
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()

    chunks_file = dir_a / "canonical_chunks.jsonl"
    chunks_file.write_text(json.dumps(_make_chunk(chunk_id="c1")), encoding="utf-8")

    articles_file = dir_b / "canonical_articles.json"
    articles_file.write_text(json.dumps(_make_articles_json()), encoding="utf-8")

    old_chunks = settings.legal_chunks_path
    old_canon = settings.canonical_articles_path
    object.__setattr__(settings, "legal_chunks_path", str(chunks_file))
    object.__setattr__(settings, "canonical_articles_path", str(articles_file))
    try:
        get_source_catalog.cache_clear()
        with pytest.raises(SourceCatalogError, match="phải cùng thư mục release"):
            get_source_catalog()
    finally:
        object.__setattr__(settings, "legal_chunks_path", old_chunks)
        object.__setattr__(settings, "canonical_articles_path", old_canon)
        get_source_catalog.cache_clear()


def test_fail_closed_release_id_mismatch_with_settings(tmp_path):
    release_dir = tmp_path / "rel_dir"
    release_dir.mkdir()

    chunks = [
        _make_chunk(chunk_id="c1", doc_id="vn:bll-2019", article_code="20.2.LQ.169", article_number="169"),
        _make_chunk(chunk_id="c2", doc_id="vn:bll-2019", article_code="20.2.LQ.170", article_number="170"),
        _make_chunk(chunk_id="c3", doc_id="vn:nd-145", article_code="20.2.ND.15", article_number="15", source_type="NĐ"),
    ]
    chunks_file = release_dir / "canonical_chunks.jsonl"
    chunks_file.write_text("\n".join(json.dumps(c) for c in chunks), encoding="utf-8")

    articles_file = release_dir / "canonical_articles.json"
    articles_file.write_text(
        json.dumps(_make_articles_json(release_id="mismatched-release-id-999")),
        encoding="utf-8",
    )

    old_chunks = settings.legal_chunks_path
    old_canon = settings.canonical_articles_path
    object.__setattr__(settings, "legal_chunks_path", str(chunks_file))
    object.__setattr__(settings, "canonical_articles_path", str(articles_file))
    try:
        get_source_catalog.cache_clear()
        with pytest.raises(SourceCatalogError, match="Release ID trong canonical_articles.json không khớp"):
            get_source_catalog()
    finally:
        object.__setattr__(settings, "legal_chunks_path", old_chunks)
        object.__setattr__(settings, "canonical_articles_path", old_canon)
        get_source_catalog.cache_clear()
