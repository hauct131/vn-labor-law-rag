import uuid
import pytest
import json
from pathlib import Path
import copy

from backend.app.ingestion.legal_chunker import (
    ChunkingConfig,
    TiktokenTokenCounter,
    RegexEstimatedTokenCounter,
    get_default_token_counter,
    make_chunk_id,
    build_chunk_key,
    build_legal_chunks,
    split_oversized_legal_text,
    validate_chunks,
    build_chunking_summary
)


class WordTokenCounter:
    """Mock token counter counting words for synthetic boundary testing."""
    name = "word-test-counter"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(text.split())


# =====================================================================
# UTILITY TESTS (RETAINED FROM PROMPT 1B)
# =====================================================================

def test_module_import_no_stdout_or_file(capsys):
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_default_config():
    cfg = ChunkingConfig()
    assert cfg.target_tokens == 500
    assert cfg.max_tokens == 750
    assert cfg.fallback_overlap == 80
    assert cfg.chunker_version == "1.0.0"


def test_config_target_gt_max():
    with pytest.raises(ValueError):
        ChunkingConfig(target_tokens=600, max_tokens=500)


def test_config_overlap_negative():
    with pytest.raises(ValueError):
        ChunkingConfig(fallback_overlap=-1)


def test_config_overlap_equal_target():
    with pytest.raises(ValueError):
        ChunkingConfig(target_tokens=100, fallback_overlap=100)


def test_config_invalid_type():
    with pytest.raises(TypeError):
        ChunkingConfig(chunker_version=123)  # type: ignore


def test_config_bool_rejected_as_int():
    with pytest.raises(TypeError):
        ChunkingConfig(target_tokens=True)  # type: ignore
    with pytest.raises(TypeError):
        ChunkingConfig(max_tokens=True)  # type: ignore
    with pytest.raises(TypeError):
        ChunkingConfig(fallback_overlap=True)  # type: ignore


def test_tiktoken_empty():
    counter = TiktokenTokenCounter()
    assert counter.count("") == 0


def test_tiktoken_vietnamese():
    counter = TiktokenTokenCounter()
    tokens = counter.count("Bộ luật Lao động Việt Nam")
    assert tokens > 0


def test_tiktoken_deterministic():
    counter = TiktokenTokenCounter()
    text = "Chào thế giới"
    assert counter.count(text) == counter.count(text)


def test_tiktoken_non_string_type_error():
    counter = TiktokenTokenCounter()
    with pytest.raises(TypeError):
        counter.count(123)  # type: ignore


def test_regex_empty():
    counter = RegexEstimatedTokenCounter()
    assert counter.count("") == 0


def test_regex_vietnamese():
    counter = RegexEstimatedTokenCounter()
    text = "Bộ luật Lao động Việt Nam"
    tokens = counter.count(text)
    assert tokens > 0


def test_regex_non_string_type_error():
    counter = RegexEstimatedTokenCounter()
    with pytest.raises(TypeError):
        counter.count(123)  # type: ignore


def test_default_counter_uses_tiktoken():
    counter = get_default_token_counter()
    assert isinstance(counter, TiktokenTokenCounter)


def test_chunk_id_stable():
    key = "art_1|clause=1"
    id1 = make_chunk_id(key)
    id2 = make_chunk_id(key)
    assert id1 == id2


def test_different_keys_different_ids():
    id1 = make_chunk_id("art_1|clause=1")
    id2 = make_chunk_id("art_1|clause=2")
    assert id1 != id2


def test_valid_uuid():
    key = "art_1|preamble"
    chunk_id = make_chunk_id(key)
    parsed = uuid.UUID(chunk_id)
    assert str(parsed) == chunk_id


def test_empty_chunk_key_rejected():
    with pytest.raises(ValueError):
        make_chunk_id("")
    with pytest.raises(ValueError):
        make_chunk_id("   ")


def test_non_string_chunk_key_rejected():
    with pytest.raises(TypeError):
        make_chunk_id(123)  # type: ignore


def test_article_key():
    key = build_chunk_key(article_id="20.2.LQ.1", chunk_type="article")
    assert key == "20.2.LQ.1|article"


def test_preamble_key():
    key = build_chunk_key(article_id="20.2.LQ.1", chunk_type="preamble")
    assert key == "20.2.LQ.1|preamble"


def test_clause_key():
    key = build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1")
    assert key == "20.2.LQ.1|clause=1"


def test_repeated_clause_occurrence():
    key1 = build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", clause_occurrence=1)
    key2 = build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", clause_occurrence=2)
    assert key1 == "20.2.LQ.1|clause=1"
    assert key2 == "20.2.LQ.1|clause=1|occurrence=2"


def test_points_preserve_order():
    key = build_chunk_key(
        article_id="20.2.LQ.1",
        chunk_type="points",
        clause_number="1",
        point_labels=["d", "a", "b"]
    )
    assert key == "20.2.LQ.1|clause=1|points=d,a,b"

    key_consec = build_chunk_key(
        article_id="20.2.LQ.1",
        chunk_type="points",
        clause_number="1",
        point_labels=["a", "b", "c", "d"]
    )
    assert key_consec == "20.2.LQ.1|clause=1|points=a-d"


def test_point_occurrence_distinguishes_key():
    key1 = build_chunk_key(
        article_id="20.2.LQ.1",
        chunk_type="points",
        clause_number="1",
        point_labels=["a"],
        point_occurrences=[1]
    )
    key2 = build_chunk_key(
        article_id="20.2.LQ.1",
        chunk_type="points",
        clause_number="1",
        point_labels=["a"],
        point_occurrences=[2]
    )
    assert key1 == "20.2.LQ.1|clause=1|points=a"
    assert key2 == "20.2.LQ.1|clause=1|points=a@2"

    key3 = build_chunk_key(
        article_id="20.2.LQ.1",
        chunk_type="points",
        clause_number="1",
        point_labels=["a", "a"],
        point_occurrences=[1, 2]
    )
    assert key3 == "20.2.LQ.1|clause=1|points=a@1,a@2"


def test_point_occurrences_mismatched_length():
    with pytest.raises(ValueError):
        build_chunk_key(
            article_id="20.2.LQ.1",
            chunk_type="points",
            clause_number="1",
            point_labels=["a", "b"],
            point_occurrences=[1]
        )


def test_table_segment_key():
    key = build_chunk_key(
        article_id="20.2.LQ.1",
        chunk_type="table",
        table_index=2,
        segment_index=3
    )
    assert key == "20.2.LQ.1|table=2|segment=3"


def test_fallback_segment_key():
    key = build_chunk_key(
        article_id="20.2.LQ.1",
        chunk_type="fallback_segment",
        clause_number="2",
        segment_index=4
    )
    assert key == "20.2.LQ.1|clause=2|segment=4"


def test_invalid_chunk_type():
    with pytest.raises(ValueError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="custom_type")


def test_invalid_segment_index():
    with pytest.raises(ValueError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", segment_index=0)
    with pytest.raises(TypeError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", segment_index=True)  # type: ignore


def test_invalid_table_index():
    with pytest.raises(ValueError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="table", table_index=0)
    with pytest.raises(TypeError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="table", table_index=True)  # type: ignore


def test_invalid_occurrence():
    with pytest.raises(ValueError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", clause_occurrence=0)
    with pytest.raises(TypeError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", clause_occurrence=True)  # type: ignore


def test_key_does_not_contain_none():
    with pytest.raises(ValueError):
        build_chunk_key(article_id="20.2.None", chunk_type="article")


# =====================================================================
# SYNTHETIC TESTS (PROMPT 2 ADDITIONS)
# =====================================================================

@pytest.fixture
def base_article():
    return {
        "article_id": "art_1",
        "article_code": "LQ.1",
        "codification_code": "20.2.LQ.1",
        "article_title": "Quy định chung",
        "topic_code": "20.2",
        "topic_name": "Lao động",
        "chapter": {"number": "I", "title": "Chương Một", "anchor_id": "chap_1"},
        "section": {"number": "1", "title": "Mục Một", "anchor_id": "sec_1"},
        "source_type": "LQ",
        "source_document_id": "doc_123",
        "source_note_text": "Ghi chú nguồn",
        "source_urls": ["http://example.com/law"],
        "parser_version": "1.2.0",
        "source_sha256": "abcdef123456",
        "relations": [],
        "attachments": [],
        "content_units": []
    }


def test_short_article_creates_one_chunk(base_article):
    # Requirement 1: short article creates one article chunk
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Lời mở đầu ngắn."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=100, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "article"
    assert chunks[0]["unit_type"] == "article"
    assert chunks[0]["requires_fallback"] is False


def test_article_chunk_excludes_table(base_article):
    # Requirement 2: article chunk excludes table unit
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Nội dung chữ."},
        {"unit_id": "u2", "unit_type": "table", "text": "Nội dung bảng không được đưa vào group text."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=100, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 1
    assert "bảng" not in chunks[0]["body_text"]
    assert "u2" not in chunks[0]["source_unit_ids"]


def test_article_chunk_content_has_breadcrumb_and_no_url_or_none(base_article):
    # Requirement 3, 4, 5: breadcrumbs, no URL, no None in content
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Văn bản chính."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    chunks = build_legal_chunks(corpus)
    content = chunks[0]["content"]

    assert "Đề mục: 20.2 — Lao động" in content
    assert "Chương I — Chương Một" in content
    assert "Mục 1 — Mục Một" in content
    assert "LQ" in content
    assert "http://example.com/law" not in content
    assert "None" not in content


def test_long_article_splits_into_clause_chunks(base_article):
    # Requirement 6: long article splits into clause chunks
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một rất dài và chiếm nhiều từ."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "2", "text": "Khoản hai cũng rất dài và chiếm nhiều từ."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=35, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 2
    assert chunks[0]["chunk_type"] == "clause"
    assert chunks[1]["chunk_type"] == "clause"
    assert chunks[0]["clause_number"] == "1"
    assert chunks[1]["clause_number"] == "2"


def test_clause_continuation_stays_with_active_clause(base_article):
    # Requirement 7: clause continuation stays with active clause
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Nội dung khoản một."},
        {"unit_id": "u2", "unit_type": "clause_continuation", "text": "Nội dung tiếp diễn của khoản một."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=40, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 1
    assert "u2" in chunks[0]["source_unit_ids"]
    assert "tiếp diễn" in chunks[0]["body_text"]


def test_points_remain_in_correct_clause(base_article):
    # Requirement 8: points remain in correct clause
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một nội dung khá dài để kiểm nghiệm logic."},
        {"unit_id": "u2", "unit_type": "point", "clause_number": "1", "point_label": "a", "text": "Điểm a của một chi tiết cụ thể."},
        {"unit_id": "u3", "unit_type": "clause", "clause_number": "2", "text": "Khoản hai nội dung tương đương và bổ trợ."},
        {"unit_id": "u4", "unit_type": "point", "clause_number": "2", "point_label": "a", "text": "Điểm a của hai chi tiết cụ thể."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=50, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 2
    assert chunks[0]["chunk_type"] == "clause"
    assert chunks[1]["chunk_type"] == "clause"
    assert "Điểm a của một" in chunks[0]["body_text"]
    assert "Điểm a của hai" in chunks[1]["body_text"]


def test_repeated_clause_occurrence_has_distinct_key(base_article):
    # Requirement 9: repeated clause occurrence has distinct chunk key
    # Make text longer so that the combined article exceeds max_tokens (45)
    # but individual clauses fit comfortably (breadcrumbs 24 + body 16 = 40 <= 45).
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "unit_occurrence": 1, "text": "Khoản một lần thứ nhất có nội dung khá dài để chia clause."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "1", "unit_occurrence": 2, "text": "Khoản một lần thứ hai có nội dung khá dài để chia clause."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=45, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 2
    assert chunks[0]["chunk_key"] != chunks[1]["chunk_key"]
    assert "occurrence=2" in chunks[1]["chunk_key"]


def test_repeated_point_occurrence_has_distinct_key(base_article):
    # Requirement 10: repeated point occurrence has distinct point-group key
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một rất dài để kích hoạt point group."},
        {"unit_id": "u2", "unit_type": "point", "clause_number": "1", "point_label": "a", "unit_occurrence": 1, "text": "Điểm a lần một."},
        {"unit_id": "u3", "unit_type": "point", "clause_number": "1", "point_label": "a", "unit_occurrence": 2, "text": "Điểm a lần hai."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=32, fallback_overlap=0)  # Low limit to trigger point group splitting
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) >= 2
    point_keys = [c["chunk_key"] for c in chunks if c["chunk_type"] == "points"]
    assert len(point_keys) == 2
    assert point_keys[0] != point_keys[1]
    assert "points=a@2" in point_keys[1]


def test_preamble_attaches_to_first_clause(base_article):
    # Requirement 11: preamble attaches to first clause when candidate within max
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Lời mở đầu ngắn."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "1", "text": "Khoản một ngắn."},
        {"unit_id": "u3", "unit_type": "clause", "clause_number": "2", "text": "Khoản hai rất dài và chiếm nhiều từ để làm đầy candidate."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=35, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert chunks[0]["chunk_type"] == "clause"
    assert "Lời mở đầu" in chunks[0]["body_text"]
    assert "Khoản một" in chunks[0]["body_text"]


def test_preamble_becomes_separate_chunk_when_candidate_exceeds_max(base_article):
    # Requirement 12: preamble becomes separate chunk when candidate exceeds max
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Lời mở đầu dài và chứa nhiều từ để làm đầy candidate."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "1", "text": "Khoản một cũng rất dài và chứa nhiều từ."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    # Breadcrumbs (23) + Preamble (11) = 34. Breadcrumbs (23) + Clause (9) = 32.
    # We increase max_tokens to 40 so they fit comfortably when split, but combined (43) exceeds 40.
    config = ChunkingConfig(target_tokens=10, max_tokens=40, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 2
    assert chunks[0]["chunk_type"] == "preamble"
    assert chunks[1]["chunk_type"] == "clause"


def test_oversized_preamble_marked_requires_fallback(base_article):
    # Requirement 13: oversized preamble undergoes fallback splitting
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Lời mở đầu cực kỳ dài vượt quá toàn bộ giới hạn của max token."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "1", "text": "Khoản một."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    # max_tokens=30 avoids ValueError from too small budget, but still triggers fallback splitting for preamble (breadcrumbs 23 + preamble 13 = 36 > 30).
    config = ChunkingConfig(target_tokens=5, max_tokens=30, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    preamble_segs = [c for c in chunks if c["chunk_type"] == "fallback_segment" and c["unit_type"] == "preamble"]
    assert len(preamble_segs) > 0
    assert preamble_segs[0]["fallback_source_reason"] == "oversized_preamble"


def test_oversized_clause_without_points_marked_requires_fallback(base_article):
    # Requirement 14: oversized clause without points undergoes fallback splitting
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một cực kỳ dài không có điểm nào và vượt quá giới hạn max token."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    # max_tokens=30 triggers fallback splitting (breadcrumbs 24 + body 15 = 39 > 30)
    config = ChunkingConfig(target_tokens=5, max_tokens=30, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    clause_segs = [c for c in chunks if c["chunk_type"] == "fallback_segment" and c["unit_type"] == "clause"]
    assert len(clause_segs) > 0
    assert clause_segs[0]["fallback_source_reason"] == "oversized_clause_without_points"


def test_oversized_point_marked_requires_fallback(base_article):
    # Requirement 15: oversized point group undergoes fallback splitting
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một."},
        {"unit_id": "u2", "unit_type": "point", "clause_number": "1", "point_label": "a", "text": "Điểm a cực kỳ dài vượt quá giới hạn tối đa."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    # max_tokens=30 triggers fallback (breadcrumbs 24 + body 11 = 35 > 30)
    config = ChunkingConfig(target_tokens=5, max_tokens=30, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    pts_segs = [c for c in chunks if c["chunk_type"] == "fallback_segment" and c["unit_type"] == "point_group"]
    assert len(pts_segs) > 0
    assert pts_segs[0]["fallback_source_reason"] == "oversized_point"


def test_long_article_without_clause_marked_requires_fallback(base_article):
    # Requirement 16: long article without clause undergoes fallback splitting
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Lời mở đầu cực kỳ dài không hề chứa khoản nào trong toàn bộ điều luật này."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    # max_tokens=30 triggers fallback
    config = ChunkingConfig(target_tokens=5, max_tokens=30, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    art_segs = [c for c in chunks if c["chunk_type"] == "fallback_segment" and c["unit_type"] == "article"]
    assert len(art_segs) > 0
    assert art_segs[0]["fallback_source_reason"] == "oversized_article_without_clause"


def _join_segment_bodies(chunks: list[dict]) -> str:
    ordered = sorted(
        chunks,
        key=lambda chunk: chunk.get("segment_index") or 0,
    )
    return " ".join(
        chunk["body_text"].strip()
        for chunk in ordered
        if chunk["body_text"].strip()
    )


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def test_orphan_point_preserved(base_article):
    # Requirement 17: orphan point preserved
    base_article["topic_code"] = None
    base_article["topic_name"] = None
    base_article["chapter"] = None
    base_article["section"] = None
    base_article["article_code"] = "1"
    base_article["article_title"] = "A"
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "point", "clause_number": "99", "point_label": "a", "text": "Điểm a mồ côi."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=2, max_tokens=8, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)

    orphan_chunks = [
        chunk
        for chunk in chunks
        if chunk["chunk_type"] == "fallback_segment"
        and chunk["unit_type"] == "orphan"
        and "u1" in chunk["source_unit_ids"]
    ]

    assert len(orphan_chunks) > 0
    for idx, c in enumerate(sorted(orphan_chunks, key=lambda x: x.get("segment_index") or 0), 1):
        assert "u1" in c["source_unit_ids"]
        assert c["segment_index"] == idx
        assert any("orphan_point" in w for w in c["warnings"])

    combined = _normalize_whitespace(_join_segment_bodies(orphan_chunks))
    assert "Điểm a mồ côi." in combined


def test_unsupported_unit_preserved(base_article):
    # Requirement 18: unsupported unit preserved
    base_article["topic_code"] = None
    base_article["topic_name"] = None
    base_article["chapter"] = None
    base_article["section"] = None
    base_article["article_code"] = "1"
    base_article["article_title"] = "A"
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "strange_type", "text": "Nội dung lạ."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=2, max_tokens=8, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)

    unsupported_chunks = [
        chunk
        for chunk in chunks
        if chunk["chunk_type"] == "fallback_segment"
        and chunk["unit_type"] == "orphan"
        and "u1" in chunk["source_unit_ids"]
    ]

    assert len(unsupported_chunks) > 0
    for idx, c in enumerate(sorted(unsupported_chunks, key=lambda x: x.get("segment_index") or 0), 1):
        assert "u1" in c["source_unit_ids"]
        assert c["segment_index"] == idx
        assert any("unsupported_unit_type" in w for w in c["warnings"])

    combined = _normalize_whitespace(_join_segment_bodies(unsupported_chunks))
    assert "Nội dung lạ." in combined


def test_no_canonical_input_mutation(base_article):
    # Requirement 19: no canonical input mutation
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Văn bản."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}

    snapshot = json.dumps(corpus)
    build_legal_chunks(corpus)

    assert json.dumps(corpus) == snapshot


def test_relation_metadata_deduplicated_and_same_length(base_article):
    # Requirement 20, 21, 22: relations deduplicated, same list lengths, external relation retained
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Văn bản."}
    ]
    base_article["relations"] = [
        {"target_id": "t1", "target_code": "c1", "relation_type": "REF", "href": "h1", "same_topic": True, "target_in_corpus": True},
        {"target_id": "t1", "target_code": "c1", "relation_type": "REF", "href": "h1", "same_topic": True, "target_in_corpus": True},
        {"target_id": None, "target_code": "c2", "relation_type": "REF", "href": "h2", "same_topic": False, "target_in_corpus": False}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    chunks = build_legal_chunks(corpus)
    chunk = chunks[0]

    assert len(chunk["relation_target_ids"]) == 2
    assert chunk["relation_target_ids"][0] == "t1"
    assert chunk["relation_target_ids"][1] is None

    l = len(chunk["relation_target_ids"])
    assert len(chunk["relation_target_codes"]) == l
    assert len(chunk["relation_types"]) == l
    assert len(chunk["relation_same_topic"]) == l
    assert len(chunk["relation_target_in_corpus"]) == l


def test_attachments_copied(base_article):
    # Requirement 23: attachments copied
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Văn bản."}
    ]
    base_article["attachments"] = [
        {"attachment_id": "att1", "filename": "file.pdf", "href": "link", "file_extension": "pdf", "downloaded": True}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    chunks = build_legal_chunks(corpus)

    assert len(chunks[0]["attachment_metadata"]) == 1
    assert chunks[0]["attachment_metadata"][0]["attachment_id"] == "att1"
    assert chunks[0]["attachment_metadata"][0] is not base_article["attachments"][0]


def test_source_urls_not_in_content(base_article):
    # Requirement 24: source URLs not in content
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Văn bản chính."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    chunks = build_legal_chunks(corpus)

    assert "http://example.com/law" not in chunks[0]["content"]


def test_token_count_equals_counter_count(base_article):
    # Requirement 25: final token_count equals counter.count(content)
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Nội dung văn bản dài."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    counter = get_default_token_counter()
    chunks = build_legal_chunks(corpus, token_counter=counter)

    assert chunks[0]["token_count"] == counter.count(chunks[0]["content"])


def test_determinism_and_no_duplicates(base_article):
    # Requirement 26, 27: same input gives identical chunks, no duplicate IDs
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Nội dung khoản một."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "2", "text": "Nội dung khoản hai."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=35, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks1 = build_legal_chunks(corpus, config=config, token_counter=counter)
    chunks2 = build_legal_chunks(corpus, config=config, token_counter=counter)

    assert len(chunks1) == len(chunks2)
    for c1, c2 in zip(chunks1, chunks2):
        assert c1["chunk_id"] == c2["chunk_id"]
        assert c1["chunk_key"] == c2["chunk_key"]
        assert c1["content"] == c2["content"]

    chunk_ids = [c["chunk_id"] for c in chunks1]
    assert len(chunk_ids) == len(set(chunk_ids))


def test_all_non_table_units_covered(base_article):
    # Requirement 28: all non-table unit IDs covered
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Lời mở đầu."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "1", "text": "Khoản một."},
        {"unit_id": "u3", "unit_type": "table", "text": "Bảng biểu."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=35, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    covered_ids = []
    for c in chunks:
        covered_ids.extend(c["source_unit_ids"])

    assert "u1" in covered_ids
    assert "u2" in covered_ids
    assert "u3" not in covered_ids


def test_empty_article_does_not_create_chunk(base_article):
    # Requirement 29: empty article does not create empty text chunk
    base_article["content_units"] = []
    corpus = {"metadata": {}, "articles": [base_article]}

    chunks = build_legal_chunks(corpus)
    assert len(chunks) == 0


def test_invalid_canonical_input_rejected():
    # Requirement 30: invalid canonical input rejected
    with pytest.raises(TypeError):
        build_legal_chunks("not a dict")  # type: ignore
    with pytest.raises(ValueError):
        build_legal_chunks({"metadata": {}})  # Missing articles
    with pytest.raises(TypeError):
        build_legal_chunks({"metadata": {}, "articles": "not a list"})  # type: ignore


# =====================================================================
# REAL CORPUS INTEGRATION TEST (PROMPT 2 ADDITIONS)
# =====================================================================

@pytest.fixture(scope="session")
def real_corpus_chunks():
    path = Path("data/processed/articles_raw.json")
    if not path.is_file():
        pytest.skip("Real corpus file data/processed/articles_raw.json not found")

    with open(path, "r", encoding="utf-8") as f:
        corpus = json.load(f)

    chunks = build_legal_chunks(corpus)
    return corpus, chunks


def test_real_corpus_ingestion(real_corpus_chunks):
    corpus, chunks = real_corpus_chunks

    assert len(corpus["articles"]) == 477
    assert len(chunks) > 0

    chunk_ids = []
    chunk_keys = []

    for c in chunks:
        assert isinstance(c["chunk_id"], str)
        assert isinstance(c["chunk_key"], str)
        assert c["content"].strip() != ""
        assert any(art["article_id"] == c["parent_article_id"] for art in corpus["articles"])

        try:
            json.dumps(c)
        except TypeError as e:
            pytest.fail(f"Chunk is not JSON serializable: {e}")

        chunk_ids.append(c["chunk_id"])
        chunk_keys.append(c["chunk_key"])

        assert "source_type" in c
        assert "source_document_id" in c
        assert "source_note_text" in c
        assert "source_urls" in c
        assert "parser_version" in c
        assert "source_sha256" in c

        assert "relation_target_ids" in c
        assert "attachment_metadata" in c
        assert isinstance(c["attachment_metadata"], list)

    assert len(chunk_ids) == len(set(chunk_ids)), "Duplicate chunk IDs found in real corpus"
    assert len(chunk_keys) == len(set(chunk_keys)), "Duplicate chunk keys found in real corpus"


# =====================================================================
# PROMPT 3 FALLBACK TESTS
# =====================================================================

def test_split_oversized_legal_text_multiple_segments():
    text = "Câu một. Câu hai. Câu ba. Câu bốn."
    config = ChunkingConfig(target_tokens=3, max_tokens=10, fallback_overlap=0)
    counter = WordTokenCounter()
    segments = split_oversized_legal_text(text, config=config, token_counter=counter)
    # Target is 3 words, so it should split into multiple segments
    assert len(segments) > 1
    assert "".join(segments).replace(" ", "") == text.replace(" ", "")


def test_split_oversized_legal_text_empty():
    config = ChunkingConfig(target_tokens=3, max_tokens=10, fallback_overlap=0)
    counter = WordTokenCounter()
    assert split_oversized_legal_text("", config=config, token_counter=counter) == []
    assert split_oversized_legal_text("   ", config=config, token_counter=counter) == []
    with pytest.raises(TypeError):
        split_oversized_legal_text(123, config=config, token_counter=counter)  # type: ignore


def test_split_oversized_legal_text_vietnamese_unicode():
    text = "Quyền và nghĩa vụ của người lao động Việt Nam được quy định rõ trong luật."
    config = ChunkingConfig(target_tokens=5, max_tokens=10, fallback_overlap=2)
    counter = WordTokenCounter()
    segments = split_oversized_legal_text(text, config=config, token_counter=counter)
    assert len(segments) > 1
    assert "người lao động" in text


def test_split_oversized_legal_text_order_preserved():
    text = "Một. Hai. Ba. Bốn. Năm. Sáu. Bảy."
    config = ChunkingConfig(target_tokens=2, max_tokens=5, fallback_overlap=0)
    counter = WordTokenCounter()
    segments = split_oversized_legal_text(text, config=config, token_counter=counter)
    assert segments[0].startswith("Một")
    assert segments[-1].endswith("Bảy.")


def test_split_oversized_legal_text_no_empty_segments():
    text = "Một.  .  Hai.   . Ba."
    config = ChunkingConfig(target_tokens=2, max_tokens=5, fallback_overlap=0)
    counter = WordTokenCounter()
    segments = split_oversized_legal_text(text, config=config, token_counter=counter)
    for s in segments:
        assert s.strip() != ""


def test_split_oversized_legal_text_deterministic():
    text = "Nội dung rất dài cần được chia nhỏ thành nhiều phần khác nhau."
    config = ChunkingConfig(target_tokens=3, max_tokens=5, fallback_overlap=1)
    counter = WordTokenCounter()
    segs1 = split_oversized_legal_text(text, config=config, token_counter=counter)
    segs2 = split_oversized_legal_text(text, config=config, token_counter=counter)
    assert segs1 == segs2


def test_split_oversized_legal_text_uses_supplied_token_counter():
    text = "A B C D E F G H I"
    config = ChunkingConfig(target_tokens=3, max_tokens=5, fallback_overlap=0)

    class CustomCounter:
        name = "custom"
        def count(self, t):
            # count letter count instead of words
            return len(t.replace(" ", ""))

    segs = split_oversized_legal_text(text, config=config, token_counter=CustomCounter())
    assert len(segs) > 1


def test_valid_article_chunk_unchanged(base_article):
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Lời mở đầu ngắn."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=20, max_tokens=50, fallback_overlap=0)
    counter = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "article"
    assert chunks[0]["requires_fallback"] is False


def test_valid_clause_chunk_unchanged(base_article):
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một ngắn."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "2", "text": "Khoản hai ngắn."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    # Max tokens = 40. Preamble/Breadcrumbs = 24.
    # Clause 1: 24 + 3 = 27 <= 40.
    # Clause 2: 24 + 3 = 27 <= 40.
    # Total article = 19 + 6 = 25 <= 40 -> Article Short Chunk (len=1)!
    # To force splitting but prevent fallback, set max_tokens=24.
    # Wait, if max_tokens=24, each clause is 27 > 24, which triggers fallback!
    # Let's make body text longer so total article > max_tokens, but clause <= max_tokens.
    # Clause 1 body = 16 words. Clause 2 body = 16 words.
    # Clause tokens = 24 + 16 = 40. Total art = 19 + 32 = 51. Set max_tokens = 45.
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một có nội dung rất dài để kiểm nghiệm logic chia khoản."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "2", "text": "Khoản hai có nội dung rất dài để kiểm nghiệm logic chia khoản."}
    ]
    config = ChunkingConfig(target_tokens=10, max_tokens=45, fallback_overlap=0)
    counter = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 2
    assert chunks[0]["chunk_type"] == "clause"
    assert chunks[1]["chunk_type"] == "clause"
    assert chunks[0]["requires_fallback"] is False


def test_only_requires_fallback_chunks_replaced(base_article):
    # Clause 1 is short, Clause 2 is oversized
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một ngắn."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "2", "text": "Khoản hai rất dài và chiếm nhiều từ vượt quá giới hạn tối đa."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    # Breadcrumbs (clause) = 24.
    # Clause 1: 24 + 3 = 27.
    # Clause 2: 24 + 14 = 38.
    # Set max_tokens=30.
    # Clause 1 (27 <= 30) stays valid.
    # Clause 2 (38 > 30) falls back and is replaced.
    config = ChunkingConfig(target_tokens=5, max_tokens=30, fallback_overlap=0)
    counter = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)

    # Clause 1 remains as a clause chunk.
    # Clause 2 is replaced by fallback segments.
    has_clause_1 = any(c["chunk_type"] == "clause" and c["clause_number"] == "1" for c in chunks)
    assert has_clause_1
    has_clause_2 = any(c["chunk_type"] == "clause" and c["clause_number"] == "2" for c in chunks)
    assert not has_clause_2

    clause_2_segs = [c for c in chunks if c["chunk_type"] == "fallback_segment" and c["clause_number"] == "2"]
    assert len(clause_2_segs) > 0


def test_segment_indices_start_at_1(base_article):
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một rất dài vượt quá giới hạn tối đa."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=3, max_tokens=30, fallback_overlap=0)
    counter = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)

    fallback_segs = [c for c in chunks if c["chunk_type"] == "fallback_segment"]
    assert len(fallback_segs) > 1
    assert fallback_segs[0]["segment_index"] == 1
    assert fallback_segs[1]["segment_index"] == 2


def test_fallback_keys_stable(base_article):
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một rất dài vượt quá giới hạn tối đa."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=3, max_tokens=30, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks1 = build_legal_chunks(corpus, config=config, token_counter=counter)
    chunks2 = build_legal_chunks(corpus, config=config, token_counter=counter)

    for c1, c2 in zip(chunks1, chunks2):
        assert c1["chunk_key"] == c2["chunk_key"]
        assert c1["chunk_id"] == c2["chunk_id"]


def test_original_body_covered(base_article):
    text = "Nội dung điều khoản này cực kỳ dài và phức tạp cần được bao phủ."
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": text}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=3, max_tokens=30, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    combined_body = " ".join([c["body_text"] for c in chunks if c["chunk_type"] == "fallback_segment"])
    # Clean spaces
    assert combined_body.replace(" ", "") == text.replace(" ", "")


def test_relation_metadata_preserved_in_fallback(base_article):
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một rất dài vượt quá giới hạn tối đa."}
    ]
    base_article["relations"] = [
        {"target_id": "t1", "target_code": "c1", "relation_type": "REF", "href": "h1", "same_topic": True, "target_in_corpus": True}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=3, max_tokens=30, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    for c in chunks:
        assert len(c["relation_target_ids"]) == 1
        assert c["relation_target_ids"][0] == "t1"


def test_attachment_metadata_preserved_in_fallback(base_article):
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một rất dài vượt quá giới hạn tối đa."}
    ]
    base_article["attachments"] = [
        {"attachment_id": "att1", "filename": "file.pdf", "href": "link", "file_extension": "pdf", "downloaded": True}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=3, max_tokens=30, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    for c in chunks:
        assert len(c["attachment_metadata"]) == 1
        assert c["attachment_metadata"][0]["attachment_id"] == "att1"


def test_no_uuid4_used_in_ids(base_article):
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một rất dài vượt quá giới hạn tối đa."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=3, max_tokens=30, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    for c in chunks:
        cid = c["chunk_id"]
        # UUIDv5 is derived deterministically. If we run again, we get same IDs.
        # Check UUID version is 5 (parsed UUID has version attribute)
        parsed = uuid.UUID(cid)
        assert parsed.version == 5


def test_table_chunk_source_authoritative(base_article):
    # table source chỉ lấy article["tables"], không gom content_units có unit_type == "table"
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "table", "text": "Bảng trong content unit"}
    ]
    base_article["tables"] = [
        {
            "table_id": "tbl_real",
            "headers": ["C1"],
            "rows": [["A"]]
        }
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    chunks = build_legal_chunks(corpus)
    # Bảng tbl_real phải được tạo, bảng u1 bị loại bỏ khỏi text chunks
    table_chunks = [c for c in chunks if c["chunk_type"] == "table"]
    assert len(table_chunks) == 1
    assert table_chunks[0]["table_id"] == "tbl_real"
    assert "Bảng trong content unit" not in table_chunks[0]["content"]


def test_table_small_single_chunk(base_article):
    # small table một chunk
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Lời mở đầu ngắn."}
    ]
    base_article["tables"] = [
        {
            "table_id": "tbl_small",
            "headers": ["ColA", "ColB"],
            "rows": [
                ["Val1", "Val2"]
            ]
        }
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=10, max_tokens=100, fallback_overlap=0)
    counter = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)

    table_chunks = [c for c in chunks if c["chunk_type"] == "table"]
    assert len(table_chunks) == 1
    c = table_chunks[0]
    assert c["table_id"] == "tbl_small"
    assert c["table_index"] == 1
    assert c["segment_index"] == 1
    assert c["unit_type"] == "table"
    assert c["requires_fallback"] is False
    assert c["oversized_reason"] is None


def test_stable_table_key_and_uuid(base_article):
    base_article["content_units"] = []
    base_article["tables"] = [
        {
            "table_id": "tbl_stable",
            "headers": ["C1"],
            "rows": [["A"]]
        }
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    chunks1 = build_legal_chunks(corpus)
    chunks2 = build_legal_chunks(corpus)

    assert chunks1[0]["chunk_key"] == chunks2[0]["chunk_key"]
    assert chunks1[0]["chunk_id"] == chunks2[0]["chunk_id"]
    # check v5 uuid
    parsed = uuid.UUID(chunks1[0]["chunk_id"])
    assert parsed.version == 5


def test_long_table_split_greedy(base_article):
    base_article["content_units"] = []
    base_article["tables"] = [
        {
            "table_id": "tbl_long",
            "headers": ["C1"],
            "rows": [
                ["Row1"],
                ["Row2"],
                ["Row3"],
                ["Row4"],
            ],
        }
    ]

    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(
        target_tokens=5,
        max_tokens=33,
        fallback_overlap=0,
    )
    counter = WordTokenCounter()

    chunks = build_legal_chunks(
        corpus,
        config=config,
        token_counter=counter,
    )

    table_chunks = [
        chunk
        for chunk in chunks
        if chunk["chunk_type"] == "table"
    ]

    # Bảng dài phải được chia thành nhiều segment.
    assert len(table_chunks) > 1

    # Segment index phải liên tục và bắt đầu từ 1.
    assert [
        chunk["segment_index"]
        for chunk in table_chunks
    ] == list(range(1, len(table_chunks) + 1))

    # Mỗi segment phải hợp lệ.
    for chunk in table_chunks:
        assert chunk["table_id"] == "tbl_long"
        assert chunk["table_index"] == 1
        assert chunk["token_count"] <= config.max_tokens
        assert chunk["requires_fallback"] is False
        assert chunk["oversized_reason"] is None
        assert "Cột: C1" in chunk["body_text"]

    combined_body = "\n".join(
        chunk["body_text"]
        for chunk in table_chunks
    )

    # Không mất và không lặp row.
    expected_rows = ["Row1", "Row2", "Row3", "Row4"]

    for row in expected_rows:
        assert combined_body.count(row) == 1

    # Thứ tự row được giữ nguyên.
    row_positions = [
        combined_body.index(row)
        for row in expected_rows
    ]
    assert row_positions == sorted(row_positions)

    # Key và ID không trùng.
    chunk_keys = [
        chunk["chunk_key"]
        for chunk in table_chunks
    ]
    chunk_ids = [
        chunk["chunk_id"]
        for chunk in table_chunks
    ]

    assert len(chunk_keys) == len(set(chunk_keys))
    assert len(chunk_ids) == len(set(chunk_ids))

def test_oversized_row_and_text_only_split(base_article):
    # oversized row được split, mọi chunk <= max_tokens, requires_fallback=False
    base_article["content_units"] = []
    base_article["tables"] = [
        {
            "table_id": "tbl_oversized_row",
            "headers": ["C1"],
            "rows": [
                ["Nội dung dòng cực kỳ dài vượt qua giới hạn của một chunk đơn lẻ."]
            ]
        }
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=33, fallback_overlap=0)
    counter = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)

    table_chunks = [c for c in chunks if c["chunk_type"] == "table"]
    assert len(table_chunks) > 1
    for c in table_chunks:
        assert c["token_count"] <= 33
        assert c["requires_fallback"] is False


def test_text_only_table_split(base_article):
    # text-only table được split
    base_article["content_units"] = []
    base_article["tables"] = [
        {
            "table_id": "tbl_text_only",
            "text": "Đoạn văn bản bảng cực kỳ dài dòng văn bản bảng cực kỳ dài dòng."
        }
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=33, fallback_overlap=0)
    counter = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)

    table_chunks = [c for c in chunks if c["chunk_type"] == "table"]
    assert len(table_chunks) > 1
    for c in table_chunks:
        assert c["token_count"] <= 33
        assert c["requires_fallback"] is False


def test_table_chunks_after_text_chunks(base_article):
    # table chunks nằm sau text chunks cùng article
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Lời mở đầu."}
    ]
    base_article["tables"] = [
        {
            "table_id": "tbl_1",
            "headers": ["C1"],
            "rows": [["A"]]
        }
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    chunks = build_legal_chunks(corpus)

    assert len(chunks) == 2
    assert chunks[0]["chunk_type"] == "article"
    assert chunks[1]["chunk_type"] == "table"


def test_canonical_input_not_mutated(base_article):
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Nội dung."}
    ]
    base_article["tables"] = [
        {
            "table_id": "tbl_1",
            "headers": ["C1"],
            "rows": [["A"]]
        }
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    orig = json.dumps(corpus)
    build_legal_chunks(corpus)
    assert json.dumps(corpus) == orig


def test_real_corpus_tables():
    path = Path("data/processed/articles_raw.json")
    if not path.is_file():
        pytest.skip("data/processed/articles_raw.json not found")

    with path.open("r", encoding="utf-8") as file:
        corpus = json.load(file)

    canonical_table_ids = {
        table["table_id"]
        for article in corpus["articles"]
        for table in article.get("tables", [])
        if table.get("table_id")
    }

    chunks_first = build_legal_chunks(corpus)
    chunks_second = build_legal_chunks(corpus)

    table_chunks = [
        chunk
        for chunk in chunks_first
        if chunk["chunk_type"] == "table"
    ]

    covered_table_ids = {
        chunk["table_id"]
        for chunk in table_chunks
        if chunk.get("table_id")
    }

    chunk_ids = [
        chunk["chunk_id"]
        for chunk in table_chunks
    ]

    chunk_keys = [
        chunk["chunk_key"]
        for chunk in table_chunks
    ]

    # Corpus hiện tại phải có đúng 63 bảng canonical.
    assert len(canonical_table_ids) == 63

    # Không thiếu và không xuất hiện bảng ngoài canonical corpus.
    assert covered_table_ids == canonical_table_ids

    # Stable IDs và keys không được trùng.
    assert len(chunk_ids) == len(set(chunk_ids))
    assert len(chunk_keys) == len(set(chunk_keys))

    for chunk in table_chunks:
        assert chunk["table_id"] in canonical_table_ids
        assert chunk["unit_type"] == "table"

        assert isinstance(chunk["table_index"], int)
        assert chunk["table_index"] >= 1

        assert isinstance(chunk["segment_index"], int)
        assert chunk["segment_index"] >= 1

        assert chunk["requires_fallback"] is False
        assert chunk["oversized_reason"] is None
        assert chunk["token_count"] <= 750

        assert chunk["content"].strip()
        assert chunk["body_text"].strip()

        # Mọi table chunk phải JSON serializable.
        json.dumps(chunk, ensure_ascii=False)

    # Chạy lại cùng input phải cho toàn bộ output giống nhau.
    assert chunks_first == chunks_second


def test_validation_happy_path():
    path = Path("data/processed/articles_raw.json")
    if not path.is_file():
        pytest.skip("data/processed/articles_raw.json not found")

    with path.open("r", encoding="utf-8") as file:
        corpus = json.load(file)

    chunks = build_legal_chunks(corpus)
    cfg = ChunkingConfig()
    tc = get_default_token_counter()
    validation = validate_chunks(corpus, chunks, config=cfg, token_counter=tc)

    assert validation["is_valid"] is True
    assert validation["article_count"] == 477
    assert validation["chunk_count"] == len(chunks)
    assert validation["covered_article_count"] == 477
    assert validation["canonical_non_table_unit_count"] == 2797
    assert validation["covered_non_table_unit_count"] == 2797
    assert validation["canonical_table_count"] == 63
    assert validation["covered_table_count"] == 63
    assert validation["duplicate_chunk_id_count"] == 0
    assert validation["duplicate_chunk_key_count"] == 0
    assert validation["requires_fallback_count"] == 0
    assert validation["oversized_chunk_count"] == 0
    assert validation["stable_id_check"] is True
    assert validation["input_mutated"] is False
    assert validation["input_chunk_count_preserved"] is True

    # JSON serializable and deterministic
    out1 = json.dumps(validation, sort_keys=True, ensure_ascii=False)
    out2 = json.dumps(validation, sort_keys=True, ensure_ascii=False)
    assert out1 == out2


def test_validation_errors(base_article):
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Nội dung ngắn."}
    ]
    corpus = {"metadata": {"document_id": "doc123", "topic_code": "LD"}, "articles": [base_article]}
    chunks = build_legal_chunks(corpus)

    # 1. Empty chunks
    val_empty = validate_chunks(corpus, [])
    assert val_empty["is_valid"] is False
    assert any("chunks_is_empty" in err for err in val_empty["errors"])

    # 2. Duplicate chunk_id
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks.append(copy.deepcopy(bad_chunks[0]))
        val_dup_id = validate_chunks(corpus, bad_chunks)
        assert val_dup_id["is_valid"] is False
        assert val_dup_id["duplicate_chunk_id_count"] == 1

    # 3. Duplicate chunk_key
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks.append(copy.deepcopy(bad_chunks[0]))
        bad_chunks[-1]["chunk_id"] = "00000000-0000-0000-0000-000000000000"
        val_dup_key = validate_chunks(corpus, bad_chunks)
        assert val_dup_key["is_valid"] is False
        assert val_dup_key["duplicate_chunk_key_count"] == 1

    # 4. Missing required field
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0].pop("tokenizer_name")
        val_missing_field = validate_chunks(corpus, bad_chunks)
        assert val_missing_field["is_valid"] is False
        assert val_missing_field["missing_required_field_count"] == 1

    # 5. Empty content
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["content"] = " "
        val_empty_content = validate_chunks(corpus, bad_chunks)
        assert val_empty_content["is_valid"] is False
        assert val_empty_content["empty_content_count"] == 1

    # 6. Empty body
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["body_text"] = "None"
        val_empty_body = validate_chunks(corpus, bad_chunks)
        assert val_empty_body["is_valid"] is False
        assert val_empty_body["empty_body_count"] == 1

    # 7. Invalid parent article ID
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["parent_article_id"] = "non_existent"
        val_invalid_parent = validate_chunks(corpus, bad_chunks)
        assert val_invalid_parent["is_valid"] is False
        assert val_invalid_parent["invalid_parent_count"] == 1
        assert "non_existent" in val_invalid_parent["invalid_parent_article_ids"]

    # 8. Missing article coverage
    bad_corpus = copy.deepcopy(corpus)
    bad_corpus["articles"].append({"article_id": "art_missing", "content_units": []})
    val_missing_cov = validate_chunks(bad_corpus, chunks)
    assert val_missing_cov["is_valid"] is False
    assert "art_missing" in val_missing_cov["missing_article_ids"]

    # 9. Invalid chunk type
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["chunk_type"] = "invalid_type"
        val_chunk_type = validate_chunks(corpus, bad_chunks)
        assert val_chunk_type["is_valid"] is False
        assert val_chunk_type["invalid_chunk_type_count"] == 1

    # 10. Invalid unit type
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["unit_type"] = "invalid_unit"
        val_unit_type = validate_chunks(corpus, bad_chunks)
        assert val_unit_type["is_valid"] is False
        assert val_unit_type["invalid_unit_type_count"] == 1

    # 11. Token count mismatch
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["token_count"] = 9999
        val_tok_mismatch = validate_chunks(corpus, bad_chunks)
        assert val_tok_mismatch["is_valid"] is False
        assert val_tok_mismatch["token_count_mismatch_count"] == 1

    # 12. Tokenizer name mismatch
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["tokenizer_name"] = "wrong_tokenizer"
        val_tokenizer_mismatch = validate_chunks(corpus, bad_chunks)
        assert val_tokenizer_mismatch["is_valid"] is False
        assert val_tokenizer_mismatch["tokenizer_name_mismatch_count"] == 1

    # 13. Oversized chunk
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["token_count"] = 5
        config_small = ChunkingConfig(target_tokens=2, max_tokens=3, fallback_overlap=0)
        val_oversized = validate_chunks(corpus, bad_chunks, config=config_small)
        assert val_oversized["is_valid"] is False
        assert val_oversized["oversized_chunk_count"] == 1

    # 14. Requires fallback true
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["requires_fallback"] = True
        val_req_fb = validate_chunks(corpus, bad_chunks)
        assert val_req_fb["is_valid"] is False
        assert val_req_fb["requires_fallback_count"] == 1

    # 15. Unstable ID (UUIDv5 mismatch)
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["chunk_id"] = str(uuid.uuid5(uuid.NAMESPACE_DNS, "wrong_dns"))
        val_unstable_id = validate_chunks(corpus, bad_chunks)
        assert val_unstable_id["is_valid"] is False
        assert val_unstable_id["stable_id_check"] is False

    # 16. Invalid UUID string
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["chunk_id"] = "not-a-valid-uuid"
        val_invalid_uuid = validate_chunks(corpus, bad_chunks)
        assert val_invalid_uuid["is_valid"] is False
        assert val_invalid_uuid["stable_id_check"] is False

    # 17. Relation list length mismatch
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["relation_target_ids"] = ["id1"]
        bad_chunks[0]["relation_target_codes"] = []
        val_rel_mismatch = validate_chunks(corpus, bad_chunks)
        assert val_rel_mismatch["is_valid"] is False
        assert val_rel_mismatch["relation_list_length_mismatch_count"] == 1

    # 18. Missing non-table source unit
    bad_corpus = copy.deepcopy(corpus)
    bad_corpus["articles"][0]["content_units"].append({"unit_id": "u2_missing", "unit_type": "clause", "clause_number": "2", "text": "Clause 2"})
    val_missing_unit = validate_chunks(bad_corpus, chunks)
    assert val_missing_unit["is_valid"] is False
    assert "u2_missing" in val_missing_unit["missing_non_table_unit_ids"]

    # 19. Unknown source unit in chunks
    bad_chunks = copy.deepcopy(chunks)
    if bad_chunks:
        bad_chunks[0]["source_unit_ids"] = bad_chunks[0]["source_unit_ids"] + ["unknown_u"]
        val_unknown_unit = validate_chunks(corpus, bad_chunks)
        assert val_unknown_unit["is_valid"] is False
        assert "unknown_u" in val_unknown_unit["unknown_source_unit_ids"]


def test_validation_table_errors(base_article):
    base_article["content_units"] = []
    base_article["tables"] = [
        {
            "table_id": "tbl1",
            "headers": ["C1"],
            "rows": [["A"], ["B"]]
        }
    ]
    corpus = {"metadata": {"document_id": "doc123"}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=20, max_tokens=100, fallback_overlap=0)
    counter = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)

    # 1. Missing table coverage
    bad_corpus = copy.deepcopy(corpus)
    bad_corpus["articles"][0]["tables"].append({"table_id": "tbl_missing", "headers": ["C2"], "rows": [["C"]]})
    val_missing_tbl = validate_chunks(bad_corpus, chunks)
    assert val_missing_tbl["is_valid"] is False
    assert "tbl_missing" in val_missing_tbl["missing_table_ids"]

    # 2. Unknown table in chunks
    bad_chunks = copy.deepcopy(chunks)
    tbl_chunk = [c for c in bad_chunks if c["chunk_type"] == "table"]
    if tbl_chunk:
        tbl_chunk[0]["table_id"] = "tbl_unknown"
        val_unknown_tbl = validate_chunks(corpus, bad_chunks)
        assert val_unknown_tbl["is_valid"] is False
        assert "tbl_unknown" in val_unknown_tbl["unknown_table_ids"]

    # 3. Table metadata invalid
    bad_chunks = copy.deepcopy(chunks)
    tbl_chunk = [c for c in bad_chunks if c["chunk_type"] == "table"]
    if tbl_chunk:
        tbl_chunk[0]["unit_type"] = "clause" # Should be table
        val_invalid_meta = validate_chunks(corpus, bad_chunks)
        assert val_invalid_meta["is_valid"] is False
        assert val_invalid_meta["invalid_table_metadata_count"] == 1

    # 4. Table segment sequence starts at 0 or has gap
    bad_chunks = copy.deepcopy(chunks)
    tbl_chunks = [c for c in bad_chunks if c["chunk_type"] == "table"]
    if len(tbl_chunks) > 0:
        tbl_chunks[0]["segment_index"] = 2 # segment sequence gap (starts at 2 instead of 1)
        val_seq_gap = validate_chunks(corpus, bad_chunks)
        assert val_seq_gap["is_valid"] is False
        assert val_seq_gap["table_segment_sequence_error_count"] == 1

    # 5. Duplicate table segment
    bad_chunks = copy.deepcopy(chunks)
    tbl_chunks = [c for c in bad_chunks if c["chunk_type"] == "table"]
    if len(tbl_chunks) > 0:
        dup = copy.deepcopy(tbl_chunks[0])
        dup["chunk_id"] = "00000000-0000-0000-0000-000000000001"
        dup["chunk_key"] = "dup_key_segment"
        bad_chunks.append(dup)
        val_dup_seg = validate_chunks(corpus, bad_chunks)
        assert val_dup_seg["is_valid"] is False
        assert val_dup_seg["duplicate_table_segment_key_count"] == 1

    # 6. Table ordering violation (table chunk before text chunk)
    # Dựng lại corpus có cả text và table
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Đoạn 1"}
    ]
    chunks_ordered = build_legal_chunks(corpus, config=config, token_counter=counter)
    # Hoán đổi thứ tự text chunk và table chunk
    bad_chunks_order = [c for c in chunks_ordered if c["chunk_type"] == "table"] + [c for c in chunks_ordered if c["chunk_type"] != "table"]
    val_order = validate_chunks(corpus, bad_chunks_order)
    assert val_order["is_valid"] is False
    assert val_order["table_ordering_error_count"] == 1


def test_validation_empty_and_no_table():
    corpus = {
        "metadata": {
            "document_id": "doc_empty",
            "topic_code": "LD",
            "topic_name": "Lao dong",
            "parser_version": "1.0.0",
            "source_sha256": "sha256"
        },
        "articles": [
            {
                "article_id": "art_empty",
                "article_code": "LQ.1",
                "article_title": "Empty Article",
                "content_units": []
            }
        ]
    }

    # 1. Chunks list is empty
    val_empty = validate_chunks(corpus, [])
    assert val_empty["is_valid"] is False
    assert val_empty["chunk_count"] == 0
    assert val_empty["covered_article_count"] == 0
    assert "art_empty" in val_empty["missing_article_ids"]
    assert val_empty["table_segment_sequence_error_count"] == 0
    assert val_empty["table_ordering_error_count"] == 0
    assert val_empty["JSON_serialization_error_count"] == 0

    # json.dumps does not crash
    assert json.dumps(val_empty, ensure_ascii=False)

    # 2. One valid table segment (no sequence error)
    base_art = {
        "article_id": "art_tbl",
        "article_code": "LQ.2",
        "article_title": "Table Article",
        "content_units": [],
        "tables": [
            {
                "table_id": "tbl1",
                "headers": ["Col"],
                "rows": [["Val"]]
            }
        ]
    }
    corpus_tbl = {
        "metadata": {"document_id": "doc_tbl"},
        "articles": [base_art]
    }

    # Build a single valid table chunk manually to verify no sequence/ordering errors
    tc = WordTokenCounter()
    cfg = ChunkingConfig(target_tokens=20, max_tokens=100, fallback_overlap=0)
    chunks = build_legal_chunks(corpus_tbl, config=cfg, token_counter=tc)

    val_tbl = validate_chunks(corpus_tbl, chunks, config=cfg, token_counter=tc)
    assert val_tbl["table_segment_sequence_error_count"] == 0
    assert val_tbl["table_ordering_error_count"] == 0
    assert val_tbl["is_valid"] is True


def test_build_chunking_summary():
    path = Path("data/processed/articles_raw.json")
    if not path.is_file():
        pytest.skip("data/processed/articles_raw.json not found")

    with path.open("r", encoding="utf-8") as file:
        corpus = json.load(file)

    cfg = ChunkingConfig()
    tc = get_default_token_counter()

    chunks = build_legal_chunks(
        corpus,
        config=cfg,
        token_counter=tc,
    )

    validation = validate_chunks(
        corpus,
        chunks,
        config=cfg,
        token_counter=tc,
    )

    summary = build_chunking_summary(
        corpus,
        chunks,
        validation,
        config=cfg,
        token_counter=tc,
    )

    # JSON serializable
    serialized = json.dumps(summary, ensure_ascii=False)
    assert isinstance(serialized, str)
    assert serialized

    # Metadata
    assert summary["document_id"] is not None
    assert summary["topic_code"] == corpus["metadata"]["topic_code"]
    assert summary["topic_name"] == corpus["metadata"]["topic_name"]
    assert summary["article_count"] == len(corpus["articles"])
    assert summary["chunk_count"] == len(chunks)
    assert summary["tokenizer_name"] == tc.name

    # Config
    assert summary["config"]["target_tokens"] == cfg.target_tokens
    assert summary["config"]["max_tokens"] == cfg.max_tokens
    assert summary["config"]["fallback_overlap"] == cfg.fallback_overlap
    assert summary["config"]["chunker_version"] == cfg.chunker_version

    # Count statistics
    assert sum(summary["chunk_type_counts"].values()) == len(chunks)
    assert sum(summary["unit_type_counts"].values()) == len(chunks)

    # Token statistics
    stats = summary["token_statistics"]
    token_counts = [chunk["token_count"] for chunk in chunks]

    assert stats["min"] == min(token_counts)
    assert stats["max"] == max(token_counts)
    assert stats["total"] == sum(token_counts)
    assert stats["mean"] > 0
    assert stats["median"] > 0
    assert stats["p95"] >= stats["median"]
    assert stats["max"] <= cfg.max_tokens

    # Coverage statistics
    assert summary["article_coverage"]["expected"] == 477
    assert summary["article_coverage"]["covered"] == 477
    assert summary["article_coverage"]["missing_count"] == 0
    assert summary["article_coverage"]["missing_ids"] == []

    assert summary["non_table_unit_coverage"]["expected"] == 2797
    assert summary["non_table_unit_coverage"]["covered"] == 2797
    assert summary["non_table_unit_coverage"]["missing_count"] == 0
    assert summary["non_table_unit_coverage"]["unknown_count"] == 0

    assert summary["table_coverage"]["expected"] == 63
    assert summary["table_coverage"]["covered"] == 63
    assert summary["table_coverage"]["missing_count"] == 0
    assert summary["table_coverage"]["unknown_count"] == 0

    # Fallback statistics
    expected_fallback_segments = sum(
        chunk["chunk_type"] == "fallback_segment"
        for chunk in chunks
    )

    assert (
        summary["fallback_statistics"]["fallback_segment_count"]
        == expected_fallback_segments
    )
    assert (
        summary["fallback_statistics"]["requires_fallback_count"]
        == 0
    )

    # Validation summary
    assert summary["validation"]["is_valid"] is True
    assert summary["validation"]["error_count"] == 0
    assert summary["validation"]["warning_count"] == len(
        validation["warnings"]
    )

    # Determinism
    summary_second = build_chunking_summary(
        corpus,
        chunks,
        validation,
        config=cfg,
        token_counter=tc,
    )
    assert summary == summary_second

    # Không có timestamp biến đổi
    assert "timestamp" not in summary
    assert "generated_at" not in summary
    assert "parsed_at" not in summary


@pytest.fixture
def fallback_test_article(base_article):
    base_article["article_id"] = "art_fallback"
    base_article["article_code"] = "LQ.2"
    base_article["codification_code"] = "20.2.LQ.2"
    base_article["topic_name"] = None
    base_article["chapter"] = None
    base_article["section"] = None
    base_article["source_type"] = None
    base_article["content_units"] = [
        {
            "unit_id": "art_fallback|clause=1",
            "unit_type": "clause",
            "clause_number": "1",
            "text": "1. Đây là phần mở đầu của khoản 1 quy định chung."
        },
        {
            "unit_id": "art_fallback|clause=1|point=a",
            "unit_type": "point",
            "clause_number": "1",
            "point_label": "a",
            "text": "a) Điểm a quy định về thông tin."
        },
        {
            "unit_id": "art_fallback|clause=1|point=b",
            "unit_type": "point",
            "clause_number": "1",
            "point_label": "b",
            "text": "b) Điểm b quy định về nghĩa vụ."
        },
        {
            "unit_id": "art_fallback|clause=1|point=c",
            "unit_type": "point",
            "clause_number": "1",
            "point_label": "c",
            "text": (
                "c) Điểm c quy định các trường hợp cụ thể: "
                "c1) Trường hợp người lao động tiếp tục làm việc tại doanh nghiệp; "
                "c2) Trường hợp người lao động chuyển sang địa điểm làm việc mới; "
                "c3) Các trường hợp đặc biệt khác theo quyết định của cơ quan nhà nước."
            )
        },
        {
            "unit_id": "art_fallback|clause=1|point=d",
            "unit_type": "point",
            "clause_number": "1",
            "point_label": "d",
            "text": (
                "d) Điểm d quy định trách nhiệm của người sử dụng: "
                "d1) Đảm bảo an toàn lao động tuyệt đối; "
                "d2) Hỗ trợ đào tạo nghề nghiệp cho người lao động."
            )
        }
    ]
    return base_article


def test_clause_only_fallback_segment_metadata(base_article):
    from backend.app.ingestion.legal_chunker import build_legal_chunks, ChunkingConfig
    base_article["article_id"] = "art_clause_fallback"
    base_article["article_code"] = "LQ.3"
    base_article["codification_code"] = "20.2.LQ.3"
    long_clause_text = "1. Đây là điều khoản có nội dung rất dài. " * 15
    base_article["content_units"] = [
        {
            "unit_id": "art_clause_fallback|clause=1",
            "unit_type": "clause",
            "clause_number": "1",
            "text": long_clause_text
        }
    ]
    corpus = {"metadata": {"topic_code": "20.2"}, "articles": [base_article]}
    cfg = ChunkingConfig(target_tokens=30, max_tokens=50, fallback_overlap=10)
    tc = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=cfg, token_counter=tc)

    fallback_segs = [c for c in chunks if c.get("chunk_type") == "fallback_segment"]
    assert len(fallback_segs) > 1
    for seg in fallback_segs:
        assert seg["point_labels"] == []
        assert seg["source_unit_ids"] == ["art_clause_fallback|clause=1"]
        assert seg["context_unit_ids"] == []


def test_fallback_segment_inherits_group_point_labels(fallback_test_article):
    from backend.app.ingestion.legal_chunker import build_legal_chunks, ChunkingConfig
    corpus = {"metadata": {"topic_code": "20.2"}, "articles": [fallback_test_article]}
    cfg = ChunkingConfig(target_tokens=30, max_tokens=50, fallback_overlap=10)
    tc = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=cfg, token_counter=tc)

    point_c_intro = next(
        chunk
        for chunk in chunks
        if chunk.get("point_labels") == ["c"]
        and "c) Điểm c quy định các trường hợp cụ thể:" in chunk.get("body_text", "")
    )
    assert point_c_intro["point_labels"] == ["c"]
    assert point_c_intro["source_unit_ids"] == ["art_fallback|clause=1|point=c"]


def test_fallback_subpoint_segment_loses_parent_context(fallback_test_article):
    from backend.app.ingestion.legal_chunker import build_legal_chunks, ChunkingConfig
    corpus = {"metadata": {"topic_code": "20.2"}, "articles": [fallback_test_article]}
    cfg = ChunkingConfig(target_tokens=30, max_tokens=50, fallback_overlap=10)
    tc = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=cfg, token_counter=tc)

    c1_segment = next(
        chunk
        for chunk in chunks
        if "[Nội dung]" in chunk.get("body_text", "")
        and "c1)" in chunk["body_text"].split("[Nội dung]", 1)[1]
    )
    assert "c1)" in c1_segment["body_text"]
    assert "c) Điểm c quy định các trường hợp cụ thể:" in c1_segment["body_text"]


def test_mid_sentence_start_characterization(fallback_test_article):
    from backend.app.ingestion.legal_chunker import build_legal_chunks, ChunkingConfig
    corpus = {"metadata": {"topic_code": "20.2"}, "articles": [fallback_test_article]}
    cfg = ChunkingConfig(target_tokens=30, max_tokens=50, fallback_overlap=10)
    tc = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=cfg, token_counter=tc)

    # Verify that no fallback segment starts with punctuation like :, ;, ,, after stripping leading spaces
    fallback_segs = [c for c in chunks if c.get("chunk_type") == "fallback_segment"]
    for seg in fallback_segs:
        body = seg["body_text"].strip()
        # Find the [Nội dung] part if it exists
        if "[Nội dung]" in body:
            content_part = body.split("[Nội dung]")[1].strip()
        else:
            content_part = body
        assert not content_part.startswith(":")
        assert not content_part.startswith(";")
        assert not content_part.startswith(",")


def test_desired_point_labels_precision(fallback_test_article):
    from backend.app.ingestion.legal_chunker import build_legal_chunks, ChunkingConfig
    corpus = {"metadata": {"topic_code": "20.2"}, "articles": [fallback_test_article]}
    cfg = ChunkingConfig(target_tokens=30, max_tokens=50, fallback_overlap=10)
    tc = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=cfg, token_counter=tc)

    c1_segment = next(
        chunk
        for chunk in chunks
        if "[Nội dung]" in chunk.get("body_text", "")
        and "c1)" in chunk["body_text"].split("[Nội dung]", 1)[1]
    )
    assert c1_segment["point_labels"] == ["c"]


def test_desired_parent_context_preservation(fallback_test_article):
    from backend.app.ingestion.legal_chunker import build_legal_chunks, ChunkingConfig
    corpus = {"metadata": {"topic_code": "20.2"}, "articles": [fallback_test_article]}
    cfg = ChunkingConfig(target_tokens=30, max_tokens=50, fallback_overlap=10)
    tc = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=cfg, token_counter=tc)

    c1_segment = next(
        chunk
        for chunk in chunks
        if "[Nội dung]" in chunk.get("body_text", "")
        and "c1)" in chunk["body_text"].split("[Nội dung]", 1)[1]
    )
    assert "c) Điểm c quy định các trường hợp cụ thể:" in c1_segment["body_text"]

    d1_segment = next(
        chunk
        for chunk in chunks
        if "[Nội dung]" in chunk.get("body_text", "")
        and "d1)" in chunk["body_text"].split("[Nội dung]", 1)[1]
    )
    assert "d) Điểm d quy định trách nhiệm của người sử dụng:" in d1_segment["body_text"]


def test_desired_source_provenance(fallback_test_article):
    from backend.app.ingestion.legal_chunker import build_legal_chunks, ChunkingConfig
    corpus = {"metadata": {"topic_code": "20.2"}, "articles": [fallback_test_article]}
    cfg = ChunkingConfig(target_tokens=30, max_tokens=50, fallback_overlap=10)
    tc = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=cfg, token_counter=tc)

    c1_segment = next(
        chunk
        for chunk in chunks
        if "[Nội dung]" in chunk.get("body_text", "")
        and "c1)" in chunk["body_text"].split("[Nội dung]", 1)[1]
    )
    assert c1_segment["source_unit_ids"] == ["art_fallback|clause=1|point=c"]
    assert c1_segment["context_unit_ids"] == [
        "art_fallback|clause=1",
        "art_fallback|clause=1|point=c",
    ]
    assert isinstance(c1_segment["context_unit_ids"], list)


def test_no_text_loss_in_fallback(fallback_test_article):
    from backend.app.ingestion.legal_chunker import build_legal_chunks, ChunkingConfig
    corpus = {"metadata": {"topic_code": "20.2"}, "articles": [fallback_test_article]}
    cfg = ChunkingConfig(target_tokens=30, max_tokens=50, fallback_overlap=10)
    tc = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=cfg, token_counter=tc)

    # Reconstruct the original primary source text from [Nội dung] parts
    rebuilt_text = ""
    fallback_c_segs = [c for c in chunks if c.get("chunk_key").startswith("art_fallback|clause=1|points=c|segment=")]
    for seg in fallback_c_segs:
        body = seg["body_text"]
        if "[Nội dung]" in body:
            content = body.split("[Nội dung]")[1].strip()
        else:
            content = body.strip()
        # Add a space or newline to match words
        rebuilt_text += " " + content

    original_c_text = fallback_test_article["content_units"][3]["text"]

    # Strip spaces and punctuation for comparison to ensure no text loss
    import string
    def clean(t):
        return "".join(c for c in t if c not in string.whitespace and c not in ":;,")

    assert clean(rebuilt_text) == clean(original_c_text)


def test_max_tokens_in_fallback(fallback_test_article):
    from backend.app.ingestion.legal_chunker import build_legal_chunks, ChunkingConfig
    corpus = {"metadata": {"topic_code": "20.2"}, "articles": [fallback_test_article]}
    cfg = ChunkingConfig(target_tokens=30, max_tokens=50, fallback_overlap=10)
    tc = WordTokenCounter()
    chunks = build_legal_chunks(corpus, config=cfg, token_counter=tc)

    for c in chunks:
        assert c["token_count"] <= cfg.max_tokens


def test_determinism_in_fallback(fallback_test_article):
    from backend.app.ingestion.legal_chunker import build_legal_chunks, ChunkingConfig
    corpus = {"metadata": {"topic_code": "20.2"}, "articles": [fallback_test_article]}
    cfg = ChunkingConfig(target_tokens=30, max_tokens=50, fallback_overlap=10)
    tc = WordTokenCounter()

    chunks1 = build_legal_chunks(corpus, config=cfg, token_counter=tc)
    chunks2 = build_legal_chunks(corpus, config=cfg, token_counter=tc)
    assert chunks1 == chunks2


def test_input_immutability_in_fallback(fallback_test_article):
    import copy
    from backend.app.ingestion.legal_chunker import build_legal_chunks, ChunkingConfig
    original_article = copy.deepcopy(fallback_test_article)
    corpus = {"metadata": {"topic_code": "20.2"}, "articles": [fallback_test_article]}
    cfg = ChunkingConfig(target_tokens=30, max_tokens=50, fallback_overlap=10)
    tc = WordTokenCounter()

    build_legal_chunks(corpus, config=cfg, token_counter=tc)
    assert fallback_test_article == original_article

def test_real_article_20_2_nd_3_19_has_unique_clause_chunks(
    real_corpus_chunks,
):
    _, chunks = real_corpus_chunks

    target_chunks = [
        chunk
        for chunk in chunks
        if chunk.get("article_code") == "20.2.NĐ.3.19"
        and chunk.get("clause_number") == "1"
    ]

    assert target_chunks

    chunk_keys = [
        chunk["chunk_key"]
        for chunk in target_chunks
    ]
    chunk_ids = [
        chunk["chunk_id"]
        for chunk in target_chunks
    ]

    assert len(chunk_keys) == len(set(chunk_keys))
    assert len(chunk_ids) == len(set(chunk_ids))

    fallback_chunks = [
        chunk
        for chunk in target_chunks
        if chunk.get("chunk_type") == "fallback_segment"
        and isinstance(chunk.get("segment_index"), int)
    ]
    assert fallback_chunks

    segments_by_key_base = {}
    for chunk in fallback_chunks:
        key_base = chunk["chunk_key"].rsplit("|segment=", 1)[0]
        segments_by_key_base.setdefault(key_base, []).append(
            chunk["segment_index"]
        )

    for indices in segments_by_key_base.values():
        assert sorted(indices) == list(range(1, len(indices) + 1))
