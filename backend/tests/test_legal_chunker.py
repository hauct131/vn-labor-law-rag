import uuid
import pytest
import json
from pathlib import Path

from backend.app.ingestion.legal_chunker import (
    ChunkingConfig,
    TiktokenTokenCounter,
    RegexEstimatedTokenCounter,
    get_default_token_counter,
    make_chunk_id,
    build_chunk_key,
    build_legal_chunks,
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
    # With breadcrumbs around 23 words + 9 words clause text, total 32 words.
    # Set max_tokens = 35 so each clause chunk fits comfortably, but total article (64 words) does not.
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
    # Clause 1: breadcrumbs (25) + text (17) = 42 words.
    # Clause 2: breadcrumbs (25) + text (17) = 42 words.
    # Total article: breadcrumbs (21) + text (34) = 55 words.
    # Set max_tokens = 50 so each clause fits (42 <= 50) but total article does not (55 > 50).
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
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "unit_occurrence": 1, "text": "Khoản một lần thứ nhất."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "1", "unit_occurrence": 2, "text": "Khoản một lần thứ hai."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    # Set max_tokens = 30 to force splitting article (33 words total) but fit each clause chunk (28 words)
    config = ChunkingConfig(target_tokens=5, max_tokens=30, fallback_overlap=0)
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
    # Max tokens = 35. Preamble (3) + Clause 1 (3) + breadcrumbs (23) = 29. Combined fits!
    # Whole article (29 + 11 = 40) does not fit. So it splits.
    config = ChunkingConfig(target_tokens=5, max_tokens=35, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    # The first chunk should be a clause chunk containing both preamble and clause 1
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
    # Breadcrumbs (23) + Preamble (11) = 34.
    # Breadcrumbs (23) + Clause 1 (9) = 32.
    # Combined: 23 + 11 + 9 = 43.
    # Let's set max_tokens=35.
    config = ChunkingConfig(target_tokens=5, max_tokens=35, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 2
    assert chunks[0]["chunk_type"] == "preamble"
    assert chunks[1]["chunk_type"] == "clause"


def test_oversized_preamble_marked_requires_fallback(base_article):
    # Requirement 13: oversized preamble marked requires_fallback
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Lời mở đầu cực kỳ dài vượt quá toàn bộ giới hạn của max token."},
        {"unit_id": "u2", "unit_type": "clause", "clause_number": "1", "text": "Khoản một."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=15, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    preamble_chunk = [c for c in chunks if c["chunk_type"] == "preamble"][0]
    assert preamble_chunk["requires_fallback"] is True
    assert preamble_chunk["oversized_reason"] == "oversized_preamble"


def test_oversized_clause_without_points_marked_requires_fallback(base_article):
    # Requirement 14: oversized clause without points marked requires_fallback
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một cực kỳ dài không có điểm nào và vượt quá giới hạn max token."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=10, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "fallback_segment"
    assert chunks[0]["requires_fallback"] is True
    assert chunks[0]["oversized_reason"] == "oversized_clause_without_points"


def test_oversized_point_marked_requires_fallback(base_article):
    # Requirement 15: oversized point marked requires_fallback
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "clause", "clause_number": "1", "text": "Khoản một."},
        {"unit_id": "u2", "unit_type": "point", "clause_number": "1", "point_label": "a", "text": "Điểm a cực kỳ dài vượt quá giới hạn tối đa."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=10, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    pts_chunks = [c for c in chunks if c["chunk_type"] == "points"]
    assert len(pts_chunks) == 1
    assert pts_chunks[0]["requires_fallback"] is True
    assert pts_chunks[0]["oversized_reason"] == "oversized_point"


def test_long_article_without_clause_marked_requires_fallback(base_article):
    # Requirement 16: long article without clause marked requires_fallback
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "preamble", "text": "Lời mở đầu cực kỳ dài không hề chứa khoản nào trong toàn bộ điều luật này."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=5, max_tokens=10, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "fallback_segment"
    assert chunks[0]["requires_fallback"] is True
    assert chunks[0]["oversized_reason"] == "oversized_article_without_clause"


def test_orphan_point_preserved(base_article):
    # Requirement 17: orphan point preserved
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "point", "clause_number": "99", "point_label": "a", "text": "Điểm a mồ côi."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=2, max_tokens=5, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    orphan_chunk = [c for c in chunks if c["chunk_type"] == "fallback_segment" and c["unit_type"] == "orphan"][0]
    assert orphan_chunk is not None
    assert "Điểm a mồ côi" in orphan_chunk["body_text"]


def test_unsupported_unit_preserved(base_article):
    # Requirement 18: unsupported unit preserved
    base_article["content_units"] = [
        {"unit_id": "u1", "unit_type": "strange_type", "text": "Nội dung lạ."}
    ]
    corpus = {"metadata": {}, "articles": [base_article]}
    config = ChunkingConfig(target_tokens=2, max_tokens=5, fallback_overlap=0)
    counter = WordTokenCounter()

    chunks = build_legal_chunks(corpus, config=config, token_counter=counter)
    orphan_chunk = [c for c in chunks if c["chunk_type"] == "fallback_segment" and c["unit_type"] == "orphan"][0]
    assert "Nội dung lạ" in orphan_chunk["body_text"]
    assert any("unsupported_unit_type" in w for w in orphan_chunk["warnings"])


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
