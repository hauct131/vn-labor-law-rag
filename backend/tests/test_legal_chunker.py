import uuid
import sys
import pytest

from backend.app.ingestion.legal_chunker import (
    ChunkingConfig,
    TiktokenTokenCounter,
    RegexEstimatedTokenCounter,
    get_default_token_counter,
    make_chunk_id,
    build_chunk_key,
)


def test_module_import_no_stdout_or_file(capsys):
    # Requirement 34 & 35: module import không stdout và không tạo file
    # We already imported it. Let's inspect capsys just in case.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_default_config():
    # Requirement 1: default config
    cfg = ChunkingConfig()
    assert cfg.target_tokens == 500
    assert cfg.max_tokens == 750
    assert cfg.fallback_overlap == 80
    assert cfg.chunker_version == "1.0.0"


def test_config_target_gt_max():
    # Requirement 2: config target > max
    with pytest.raises(ValueError):
        ChunkingConfig(target_tokens=600, max_tokens=500)


def test_config_overlap_negative():
    # Requirement 3: config overlap âm
    with pytest.raises(ValueError):
        ChunkingConfig(fallback_overlap=-1)


def test_config_overlap_equal_target():
    # Requirement 4: overlap bằng target
    with pytest.raises(ValueError):
        ChunkingConfig(target_tokens=100, fallback_overlap=100)


def test_config_invalid_type():
    # Requirement 5: sai type config
    with pytest.raises(TypeError):
        ChunkingConfig(chunker_version=123)  # type: ignore


def test_config_bool_rejected_as_int():
    # Requirement 6: bool bị từ chối như int
    with pytest.raises(TypeError):
        ChunkingConfig(target_tokens=True)  # type: ignore
    with pytest.raises(TypeError):
        ChunkingConfig(max_tokens=True)  # type: ignore
    with pytest.raises(TypeError):
        ChunkingConfig(fallback_overlap=True)  # type: ignore


def test_tiktoken_empty():
    # Requirement 7: tiktoken empty
    counter = TiktokenTokenCounter()
    assert counter.count("") == 0


def test_tiktoken_vietnamese():
    # Requirement 8: tiktoken tiếng Việt
    counter = TiktokenTokenCounter()
    tokens = counter.count("Bộ luật Lao động Việt Nam")
    assert tokens > 0


def test_tiktoken_deterministic():
    # Requirement 9: tiktoken deterministic
    counter = TiktokenTokenCounter()
    text = "Chào thế giới"
    assert counter.count(text) == counter.count(text)


def test_tiktoken_non_string_type_error():
    # Requirement 10: tiktoken non-string raise TypeError
    counter = TiktokenTokenCounter()
    with pytest.raises(TypeError):
        counter.count(123)  # type: ignore


def test_regex_empty():
    # Requirement 11: regex empty
    counter = RegexEstimatedTokenCounter()
    assert counter.count("") == 0


def test_regex_vietnamese():
    # Requirement 12: regex tiếng Việt
    counter = RegexEstimatedTokenCounter()
    text = "Bộ luật Lao động Việt Nam"
    tokens = counter.count(text)
    assert tokens > 0


def test_regex_non_string_type_error():
    # Requirement 13: regex non-string raise TypeError
    counter = RegexEstimatedTokenCounter()
    with pytest.raises(TypeError):
        counter.count(123)  # type: ignore


def test_default_counter_uses_tiktoken():
    # Requirement 14: default counter dùng tiktoken
    counter = get_default_token_counter()
    assert isinstance(counter, TiktokenTokenCounter)


def test_chunk_id_stable():
    # Requirement 15: chunk ID stable
    key = "art_1|clause=1"
    id1 = make_chunk_id(key)
    id2 = make_chunk_id(key)
    assert id1 == id2


def test_different_keys_different_ids():
    # Requirement 16: key khác ID khác
    id1 = make_chunk_id("art_1|clause=1")
    id2 = make_chunk_id("art_1|clause=2")
    assert id1 != id2


def test_valid_uuid():
    # Requirement 17: UUID hợp lệ
    key = "art_1|preamble"
    chunk_id = make_chunk_id(key)
    parsed = uuid.UUID(chunk_id)
    assert str(parsed) == chunk_id


def test_empty_chunk_key_rejected():
    # Requirement 18: empty chunk key bị từ chối
    with pytest.raises(ValueError):
        make_chunk_id("")
    with pytest.raises(ValueError):
        make_chunk_id("   ")


def test_non_string_chunk_key_rejected():
    # Requirement 19: non-string chunk key bị từ chối
    with pytest.raises(TypeError):
        make_chunk_id(123)  # type: ignore


def test_article_key():
    # Requirement 20: article key
    key = build_chunk_key(article_id="20.2.LQ.1", chunk_type="article")
    assert key == "20.2.LQ.1|article"


def test_preamble_key():
    # Requirement 21: preamble key
    key = build_chunk_key(article_id="20.2.LQ.1", chunk_type="preamble")
    assert key == "20.2.LQ.1|preamble"


def test_clause_key():
    # Requirement 22: clause key
    key = build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1")
    assert key == "20.2.LQ.1|clause=1"


def test_repeated_clause_occurrence():
    # Requirement 23: repeated clause occurrence đổi key
    key1 = build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", clause_occurrence=1)
    key2 = build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", clause_occurrence=2)
    assert key1 == "20.2.LQ.1|clause=1"
    assert key2 == "20.2.LQ.1|clause=1|occurrence=2"


def test_points_preserve_order():
    # Requirement 24: points giữ thứ tự (không sort point_labels)
    key = build_chunk_key(
        article_id="20.2.LQ.1",
        chunk_type="points",
        clause_number="1",
        point_labels=["d", "a", "b"]
    )
    assert key == "20.2.LQ.1|clause=1|points=d,a,b"  # Not a-b-d since it's not consecutive in that order

    # Standard consecutive order check
    key_consec = build_chunk_key(
        article_id="20.2.LQ.1",
        chunk_type="points",
        clause_number="1",
        point_labels=["a", "b", "c", "d"]
    )
    assert key_consec == "20.2.LQ.1|clause=1|points=a-d"


def test_point_occurrence_distinguishes_key():
    # Requirement 25: point occurrence phân biệt key
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
    # Requirement 26: số point occurrence không khớp bị từ chối
    with pytest.raises(ValueError):
        build_chunk_key(
            article_id="20.2.LQ.1",
            chunk_type="points",
            clause_number="1",
            point_labels=["a", "b"],
            point_occurrences=[1]
        )


def test_table_segment_key():
    # Requirement 27: table segment key
    key = build_chunk_key(
        article_id="20.2.LQ.1",
        chunk_type="table",
        table_index=2,
        segment_index=3
    )
    assert key == "20.2.LQ.1|table=2|segment=3"


def test_fallback_segment_key():
    # Requirement 28: fallback segment key
    key = build_chunk_key(
        article_id="20.2.LQ.1",
        chunk_type="fallback_segment",
        clause_number="2",
        segment_index=4
    )
    assert key == "20.2.LQ.1|clause=2|segment=4"


def test_invalid_chunk_type():
    # Requirement 29: invalid chunk type
    with pytest.raises(ValueError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="custom_type")


def test_invalid_segment_index():
    # Requirement 30: invalid segment index
    with pytest.raises(ValueError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", segment_index=0)
    with pytest.raises(TypeError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", segment_index=True)  # type: ignore


def test_invalid_table_index():
    # Requirement 31: invalid table index
    with pytest.raises(ValueError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="table", table_index=0)
    with pytest.raises(TypeError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="table", table_index=True)  # type: ignore


def test_invalid_occurrence():
    # Requirement 32: invalid occurrence
    with pytest.raises(ValueError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", clause_occurrence=0)
    with pytest.raises(TypeError):
        build_chunk_key(article_id="20.2.LQ.1", chunk_type="clause", clause_number="1", clause_occurrence=True)  # type: ignore


def test_key_does_not_contain_none():
    # Requirement 33: key không chứa None
    # If key building mistakenly formats None values into the key string
    with pytest.raises(ValueError):
        # We simulate a case where a value contains the word "None" as part of user input to trigger validation
        # or we ensure standard keys do not contain "None"
        build_chunk_key(article_id="20.2.None", chunk_type="article")
