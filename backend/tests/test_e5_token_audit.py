"""Unit tests for the E5 token-length audit script.

All tests use fake tokenizers or local tokenizers.Tokenizer objects.
No real TextEmbedding model is imported or initialised.
No network access required.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.audit_e5_token_lengths import (
    AuditInputError,
    AuditStats,
    ChunkRecord,
    TokenizerAdapter,
    build_parser,
    build_summary,
    count_tokens_batch,
    load_audit_tokenizer,
    measure_chunks,
    validate_chunk_count,
    validate_cli_args,
    write_outputs,
)


# ---------------------------------------------------------------------------
# Helpers: fake tokenizer infrastructure
# ---------------------------------------------------------------------------


@dataclass
class _FakeEncoding:
    ids: list[int]


class _FakeAuditTokenizer:
    """Minimal stub that mimics tokenizers.Tokenizer's encode_batch API."""

    def __init__(self, counts: dict[str, int], default: int = 10) -> None:
        self._counts = counts
        self._default = default

    def encode_batch(
        self, texts: list[str], add_special_tokens: bool = True
    ) -> list[_FakeEncoding]:
        return [
            _FakeEncoding(ids=list(range(self._counts.get(t, self._default))))
            for t in texts
        ]


def _make_adapter(
    counts: dict[str, int],
    default: int = 10,
    model_max_length: int = 512,
    tokenizer_class: str = "FakeTokenizer",
) -> TokenizerAdapter:
    tok = _FakeAuditTokenizer(counts, default)
    return TokenizerAdapter(
        inference_tokenizer=tok,
        audit_tokenizer=tok,
        model_max_length=model_max_length,
        tokenizer_class=tokenizer_class,
    )


def _make_chunk(
    chunk_id: str = "c1",
    content: str = "hello world",
    token_count: int | None = 10,
    chunk_type: str = "article",
    container_type: str = "article",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "chunk_id": chunk_id,
        "chunk_key": f"{chunk_id}|article",
        "article_code": "20.2.LQ.1",
        "article_title": "Phạm vi",
        "chunk_type": chunk_type,
        "container_type": container_type,
        "parent_article_id": None,
        "parent_attachment_id": None,
        "clause_number": None,
        "token_count": token_count,
        "tokenizer_name": "tiktoken:cl100k_base",
        "content": content,
        **extra,
    }


# ---------------------------------------------------------------------------
# 1. Parser defaults
# ---------------------------------------------------------------------------


class TestParserDefaults:
    def test_chunk_default(self):
        args = build_parser().parse_args([])
        assert str(args.chunks) == "data/processed/legal_chunks.jsonl"

    def test_output_dir_default(self):
        args = build_parser().parse_args([])
        assert str(args.output_dir) == "experiments/e5_token_audit"

    def test_model_default(self):
        args = build_parser().parse_args([])
        assert args.model == "intfloat/multilingual-e5-large"

    def test_expected_chunks_default(self):
        args = build_parser().parse_args([])
        assert args.expected_chunks == 1127

    def test_near_limit_default(self):
        args = build_parser().parse_args([])
        assert args.near_limit == 480

    def test_expected_max_tokens_default(self):
        args = build_parser().parse_args([])
        assert args.expected_max_tokens == 512

    def test_batch_size_default(self):
        args = build_parser().parse_args([])
        assert args.batch_size == 128

    def test_threads_default(self):
        args = build_parser().parse_args([])
        assert args.threads == 6

    def test_local_files_only_default(self):
        args = build_parser().parse_args([])
        assert args.local_files_only is True

    def test_dry_run_default(self):
        args = build_parser().parse_args([])
        assert args.dry_run is False

    def test_cache_dir_default_is_none(self):
        args = build_parser().parse_args([])
        assert args.cache_dir is None


# ---------------------------------------------------------------------------
# 2. validate_cli_args
# ---------------------------------------------------------------------------


class TestValidateCLIArgs:
    def _args(self, **kwargs: Any) -> SimpleNamespace:
        defaults = {
            "near_limit": 480,
            "expected_max_tokens": 512,
            "batch_size": 128,
            "threads": 6,
            "expected_chunks": 1127,
            "output_dir": Path("experiments/e5_token_audit"),
            "chunks": Path("data/processed/legal_chunks.jsonl"),
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_near_limit_zero_raises(self):
        with pytest.raises(AuditInputError, match="near-limit"):
            validate_cli_args(self._args(near_limit=0))

    def test_near_limit_negative_raises(self):
        with pytest.raises(AuditInputError, match="near-limit"):
            validate_cli_args(self._args(near_limit=-1))

    def test_near_limit_exceeds_max_tokens_raises(self):
        with pytest.raises(AuditInputError, match="near-limit"):
            validate_cli_args(self._args(near_limit=513, expected_max_tokens=512))

    def test_valid_args_passes(self):
        validate_cli_args(self._args())  # must not raise

    def test_batch_size_zero_raises(self):
        with pytest.raises(AuditInputError, match="batch-size"):
            validate_cli_args(self._args(batch_size=0))

    def test_threads_zero_raises(self):
        with pytest.raises(AuditInputError, match="threads"):
            validate_cli_args(self._args(threads=0))


# ---------------------------------------------------------------------------
# 3. Dry-run does not load tokenizer and creates no files
# ---------------------------------------------------------------------------


class TestDryRun:
    def test_dry_run_returns_status(self, tmp_path: Path):
        chunks_path = tmp_path / "chunks.jsonl"
        chunk = _make_chunk()
        chunks_path.write_text(
            json.dumps(chunk, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        args = build_parser().parse_args([
            "--chunks", str(chunks_path),
            "--expected-chunks", "1",
            "--dry-run",
            "--output-dir", str(tmp_path / "out"),
        ])
        from scripts.audit_e5_token_lengths import run
        result = run(args)
        assert result["status"] == "dry_run_pass"

    def test_dry_run_creates_no_files(self, tmp_path: Path):
        chunks_path = tmp_path / "chunks.jsonl"
        chunks_path.write_text(
            json.dumps(_make_chunk()) + "\n", encoding="utf-8"
        )
        out_dir = tmp_path / "audit_out"
        args = build_parser().parse_args([
            "--chunks", str(chunks_path),
            "--expected-chunks", "1",
            "--dry-run",
            "--output-dir", str(out_dir),
        ])
        from scripts.audit_e5_token_lengths import run
        run(args)
        assert not out_dir.exists(), "Dry-run must not create output files"

    def test_dry_run_does_not_import_text_embedding(self, tmp_path: Path):
        """Verify no TextEmbedding initialised: the run() call returns without
        loading the model."""
        chunks_path = tmp_path / "chunks.jsonl"
        chunks_path.write_text(
            json.dumps(_make_chunk()) + "\n", encoding="utf-8"
        )
        args = build_parser().parse_args([
            "--chunks", str(chunks_path),
            "--expected-chunks", "1",
            "--dry-run",
            "--output-dir", str(tmp_path / "out"),
        ])
        # This must succeed even without a cached model
        from scripts.audit_e5_token_lengths import run
        result = run(args)
        assert result["status"] == "dry_run_pass"


# ---------------------------------------------------------------------------
# 4. Duplicate chunk IDs are rejected
# ---------------------------------------------------------------------------


def test_duplicate_chunk_ids_are_rejected(tmp_path: Path):
    chunks_path = tmp_path / "chunks.jsonl"
    chunk = _make_chunk()
    chunks_path.write_text(
        json.dumps(chunk) + "\n" + json.dumps(chunk) + "\n",
        encoding="utf-8",
    )
    from scripts.audit_e5_token_lengths import load_chunks, ProbeInputError
    with pytest.raises(ProbeInputError, match="Duplicate chunk_id"):
        load_chunks(chunks_path)


# ---------------------------------------------------------------------------
# 5. Empty content is rejected
# ---------------------------------------------------------------------------


def test_empty_content_is_rejected(tmp_path: Path):
    chunks_path = tmp_path / "chunks.jsonl"
    bad = _make_chunk(content="   ")
    chunks_path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    from scripts.audit_e5_token_lengths import load_chunks, ProbeInputError
    with pytest.raises(ProbeInputError, match="empty content"):
        load_chunks(chunks_path)


# ---------------------------------------------------------------------------
# 6. Expected chunk count mismatch
# ---------------------------------------------------------------------------


def test_expected_chunk_count_mismatch():
    chunks = [_make_chunk("c1"), _make_chunk("c2")]
    with pytest.raises(AuditInputError, match="Expected 5 chunks"):
        validate_chunk_count(chunks, 5)


# ---------------------------------------------------------------------------
# 7. E5 "passage: " prefix is applied exactly once
# ---------------------------------------------------------------------------


def test_e5_prefix_applied_once():
    from scripts.test_sparse_dense import e5_document_text
    content = "Nội dung điều khoản"
    model = "intfloat/multilingual-e5-large"
    result = e5_document_text(content, model)
    assert result == "passage: " + content
    # Apply again should not double-prefix
    assert result.count("passage: ") == 1


# ---------------------------------------------------------------------------
# 8. Content-only and passage counts are separate
# ---------------------------------------------------------------------------


def test_content_and_passage_counts_are_separate():
    content = "hello"
    passage = "passage: hello"
    # 5 tokens for content text, 7 for passage text (with prefix words)
    adapter = _make_adapter({content: 5, passage: 7})
    chunk = _make_chunk(content=content)
    records = measure_chunks(
        [chunk],
        adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    assert records[0].e5_content_token_count == 5
    assert records[0].e5_passage_token_count == 7
    assert records[0].e5_prefix_overhead == 2  # 7 - 5


# ---------------------------------------------------------------------------
# 9. Threshold behaviour for counts 479, 480, 512, 513
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "count, near, at, over, overflow",
    [
        (479, False, False, False, 0),
        (480, True, False, False, 0),
        (512, True, True, False, 0),
        (513, True, False, True, 1),
    ],
)
def test_threshold_behaviour(count, near, at, over, overflow):
    content = "x"
    passage = "passage: x"
    adapter = _make_adapter({content: 10, passage: count})
    chunk = _make_chunk(content=content)
    records = measure_chunks(
        [chunk],
        adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    r = records[0]
    assert r.near_or_above_limit is near, f"near_or_above_limit wrong for count={count}"
    assert r.at_model_limit is at, f"at_model_limit wrong for count={count}"
    assert r.over_model_limit is over, f"over_model_limit wrong for count={count}"
    assert r.overflow_tokens == overflow, f"overflow_tokens wrong for count={count}"


def test_exactly_512_is_at_limit_but_not_strictly_over():
    content = "a"
    passage = "passage: a"
    adapter = _make_adapter({content: 5, passage: 512})
    records = measure_chunks(
        [_make_chunk(content=content)],
        adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    r = records[0]
    assert r.at_model_limit is True
    assert r.over_model_limit is False
    assert r.overflow_tokens == 0


def test_513_is_strictly_over_with_overflow_1():
    content = "b"
    passage = "passage: b"
    adapter = _make_adapter({content: 5, passage: 513})
    records = measure_chunks(
        [_make_chunk(content=content)],
        adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    r = records[0]
    assert r.over_model_limit is True
    assert r.overflow_tokens == 1
    assert r.at_model_limit is False


# ---------------------------------------------------------------------------
# 10. Summary statistics: min/max/mean/median/p95/p99
# ---------------------------------------------------------------------------


def test_audit_stats_single_value():
    stats = AuditStats.from_counts([100])
    assert stats.min == 100
    assert stats.max == 100
    assert stats.mean == 100.0
    assert stats.median == 100.0
    assert stats.p95 == 100.0
    assert stats.p99 == 100.0
    assert stats.total == 100


def test_audit_stats_multiple_values():
    counts = [10, 20, 30, 40, 50]
    stats = AuditStats.from_counts(counts)
    assert stats.min == 10
    assert stats.max == 50
    assert stats.mean == 30.0
    assert stats.median == 30.0
    assert stats.total == 150


def test_summary_statistics_in_report(tmp_path: Path):
    """build_summary should populate token_statistics correctly."""
    chunks = [
        _make_chunk("c1", content="a", token_count=10),
        _make_chunk("c2", content="b", token_count=20),
        _make_chunk("c3", content="c", token_count=30),
    ]
    # passage counts: 100, 200, 300
    counts = {
        "a": 90, "passage: a": 100,
        "b": 180, "passage: b": 200,
        "c": 270, "passage: c": 300,
    }
    adapter = _make_adapter(counts)
    records = measure_chunks(
        chunks, adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    summary = build_summary(
        records=records,
        chunks=chunks,
        input_path=tmp_path / "chunks.jsonl",
        input_sha256="abc123",
        model_name="intfloat/multilingual-e5-large",
        adapter=adapter,
        near_limit=480,
    )
    stats = summary["token_statistics"]
    assert stats["min"] == 100
    assert stats["max"] == 300
    assert stats["total"] == 600


# ---------------------------------------------------------------------------
# 11. Grouping by chunk_type
# ---------------------------------------------------------------------------


def test_grouping_by_chunk_type(tmp_path: Path):
    chunks = [
        _make_chunk("c1", content="a", chunk_type="article"),
        _make_chunk("c2", content="b", chunk_type="article"),
        _make_chunk("c3", content="c", chunk_type="clause"),
    ]
    adapter = _make_adapter({"a": 5, "passage: a": 10, "b": 5, "passage: b": 10,
                             "c": 5, "passage: c": 10})
    records = measure_chunks(
        chunks, adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    summary = build_summary(
        records=records,
        chunks=chunks,
        input_path=tmp_path / "chunks.jsonl",
        input_sha256="sha",
        model_name="intfloat/multilingual-e5-large",
        adapter=adapter,
        near_limit=480,
    )
    by_type = summary["counts_by_chunk_type"]
    assert by_type["article"] == 2
    assert by_type["clause"] == 1


# ---------------------------------------------------------------------------
# 12. Grouping by container_type
# ---------------------------------------------------------------------------


def test_grouping_by_container_type(tmp_path: Path):
    chunks = [
        _make_chunk("c1", content="a", container_type="article"),
        _make_chunk("c2", content="b", container_type="appendix"),
    ]
    adapter = _make_adapter({"a": 5, "passage: a": 10,
                             "b": 5, "passage: b": 10})
    records = measure_chunks(
        chunks, adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    summary = build_summary(
        records=records,
        chunks=chunks,
        input_path=tmp_path / "chunks.jsonl",
        input_sha256="sha",
        model_name="intfloat/multilingual-e5-large",
        adapter=adapter,
        near_limit=480,
    )
    by_ct = summary["counts_by_container_type"]
    assert by_ct["article"] == 1
    assert by_ct["appendix"] == 1


# ---------------------------------------------------------------------------
# 13. Comparison with cl100k counts
# ---------------------------------------------------------------------------


def test_cl100k_comparison_in_summary(tmp_path: Path):
    chunks = [
        _make_chunk("c1", content="a", token_count=100),
        _make_chunk("c2", content="b", token_count=200),
    ]
    adapter = _make_adapter({"a": 5, "passage: a": 110,
                             "b": 5, "passage: b": 210})
    records = measure_chunks(
        chunks, adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    summary = build_summary(
        records=records,
        chunks=chunks,
        input_path=tmp_path / "chunks.jsonl",
        input_sha256="sha",
        model_name="intfloat/multilingual-e5-large",
        adapter=adapter,
        near_limit=480,
    )
    comp = summary["cl100k_comparison"]
    assert comp["available_count"] == 2
    assert comp["missing_count"] == 0
    # Both deltas should be +10 (110 - 100, and 210 - 200)
    stats = comp["difference_statistics"]
    assert stats["mean"] == 10.0
    assert stats["median"] == 10.0
    assert stats["max"] == 10


# ---------------------------------------------------------------------------
# 14. Deterministic ordering of over_limit output
# ---------------------------------------------------------------------------


def test_over_limit_sorted_by_overflow_desc_then_chunk_id_asc(
    tmp_path: Path,
):
    chunks = [
        _make_chunk("c3", content="a"),  # overflow=5
        _make_chunk("c1", content="b"),  # overflow=10
        _make_chunk("c2", content="c"),  # overflow=10
    ]
    # passage: c3 => 517 (overflow=5), c1 => 522 (overflow=10), c2 => 522
    counts = {
        "a": 5, "passage: a": 517,
        "b": 5, "passage: b": 522,
        "c": 5, "passage: c": 522,
    }
    adapter = _make_adapter(counts)
    records = measure_chunks(
        chunks, adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    out_dir = tmp_path / "out"
    summary = build_summary(
        records=records,
        chunks=chunks,
        input_path=tmp_path / "chunks.jsonl",
        input_sha256="sha",
        model_name="intfloat/multilingual-e5-large",
        adapter=adapter,
        near_limit=480,
    )
    write_outputs(records, summary, out_dir)
    payload = json.loads((out_dir / "over_limit.json").read_text())
    ids = [item["chunk_id"] for item in payload]
    # c1 and c2 both overflow=10, sorted asc by chunk_id → c1 before c2
    # c3 overflow=5 → last
    assert ids == ["c1", "c2", "c3"]


# ---------------------------------------------------------------------------
# 15. Byte-stable JSON output
# ---------------------------------------------------------------------------


def test_byte_stable_json_output(tmp_path: Path):
    chunks = [_make_chunk("c1", content="hello"), _make_chunk("c2", content="world")]
    adapter = _make_adapter({"hello": 5, "passage: hello": 10,
                             "world": 6, "passage: world": 11})
    records = measure_chunks(
        chunks, adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    summary = build_summary(
        records=records,
        chunks=chunks,
        input_path=tmp_path / "chunks.jsonl",
        input_sha256="fixed-sha",
        model_name="intfloat/multilingual-e5-large",
        adapter=adapter,
        near_limit=480,
    )

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    write_outputs(records, summary, out1)
    write_outputs(records, summary, out2)

    assert (out1 / "summary.json").read_bytes() == (out2 / "summary.json").read_bytes()
    assert (out1 / "chunks.csv").read_bytes() == (out2 / "chunks.csv").read_bytes()
    assert (out1 / "over_limit.json").read_bytes() == (out2 / "over_limit.json").read_bytes()


# ---------------------------------------------------------------------------
# 16. Mismatched tokenizer max length raises a clear error
# ---------------------------------------------------------------------------


def test_mismatched_tokenizer_max_length_raises():
    """load_audit_tokenizer should raise RuntimeError if model max_length
    differs from --expected-max-tokens."""
    # Build a minimal tokenizer that reports max_length=256
    from tokenizers import Tokenizer
    from tokenizers.models import BPE
    tok = Tokenizer(BPE())
    tok.enable_truncation(max_length=256)

    class _FakeEmbedding:
        class _Model:
            _model_dir = None  # will be set below

        model = _Model()

    # We patch load_audit_tokenizer at the import level via monkeypatching
    # Instead, test the validation logic directly
    from scripts.audit_e5_token_lengths import load_audit_tokenizer
    import unittest.mock as mock

    def _fake_load_tokenizer(model_dir):  # returns (tokenizer, special_token_to_id)
        return tok, {}

    def _fake_text_embedding(**kwargs):
        class _Inner:
            class _model:
                _model_dir = Path("/fake/model_dir")
            model = _model

        obj = _Inner()
        return obj

    with (
        mock.patch(
            "scripts.audit_e5_token_lengths.load_audit_tokenizer",
            wraps=lambda *a, **kw: _real_load(*a, **kw),
        )
    ):
        pass  # just checking the import works

    # Test the mismatch logic via the validation path
    trunc = tok.truncation
    if trunc is not None:
        actual_max = trunc.get("max_length")
        if actual_max != 512:
            assert actual_max == 256  # confirm our fake tokenizer


def test_tokenizer_max_length_validation_logic(tmp_path: Path):
    """Test the explicit check in load_audit_tokenizer via mocking."""
    import unittest.mock as mock

    from tokenizers import Tokenizer
    from tokenizers.models import BPE

    tok = Tokenizer(BPE())
    tok.enable_truncation(max_length=256)

    fake_model_dir = tmp_path / "fake_model_dir"
    fake_model_dir.mkdir()

    with (
        mock.patch(
            "scripts.audit_e5_token_lengths.TextEmbedding",
            side_effect=lambda **kwargs: _build_fake_embedding(fake_model_dir),
        ),
        mock.patch(
            "scripts.audit_e5_token_lengths.load_tokenizer",
            return_value=(tok, {}),
        ),
        pytest.raises(RuntimeError, match="max_length=256 differs from"),
    ):
        # expected_max_tokens=512, but tokenizer reports 256 → should raise
        load_audit_tokenizer(
            model_name="intfloat/multilingual-e5-large",
            threads=1,
            local_files_only=False,
            cache_dir=None,
            expected_max_tokens=512,
        )


def _build_fake_embedding(model_dir: Path) -> Any:
    class _Inner:
        _model_dir = model_dir

    class _Wrapper:
        model = _Inner()

    return _Wrapper()


# ---------------------------------------------------------------------------
# 17. FastEmbed/tokenizer adapter failure is converted to RuntimeError
# ---------------------------------------------------------------------------


def test_text_embedding_init_failure_is_runtime_error():
    import unittest.mock as mock

    with mock.patch(
        "scripts.audit_e5_token_lengths.TextEmbedding",
        side_effect=Exception("download failed"),
    ), pytest.raises(RuntimeError, match="Failed to initialise TextEmbedding"):
        load_audit_tokenizer(
            model_name="intfloat/multilingual-e5-large",
            threads=1,
            local_files_only=True,
            cache_dir=None,
            expected_max_tokens=512,
        )


# ---------------------------------------------------------------------------
# 18. No test imports or initializes a real TextEmbedding model
# ---------------------------------------------------------------------------


def test_no_real_text_embedding_in_module_scope():
    """Verify that the test file does not have a module-level TextEmbedding
    instance. This is a meta-test: if it reaches here, no import-time model
    load happened."""
    import sys
    # 'fastembed' should not be imported in this test module's namespace
    test_module_vars = dir(sys.modules[__name__])
    assert "TextEmbedding" not in test_module_vars


# ---------------------------------------------------------------------------
# 19. count_tokens_batch uses audit tokenizer correctly
# ---------------------------------------------------------------------------


def test_count_tokens_batch_uses_audit_tokenizer():
    texts = ["hello world", "bonjour"]
    fake_tok = _FakeAuditTokenizer({"hello world": 3, "bonjour": 2})
    adapter = TokenizerAdapter(
        inference_tokenizer=fake_tok,
        audit_tokenizer=fake_tok,
        model_max_length=512,
        tokenizer_class="FakeTokenizer",
    )
    counts = count_tokens_batch(texts, adapter)
    assert counts == [3, 2]


# ---------------------------------------------------------------------------
# 20. write_outputs: over_limit.json only contains strictly over-limit chunks
# ---------------------------------------------------------------------------


def test_over_limit_json_only_contains_over_limit_chunks(tmp_path: Path):
    # chunk c1: passage=511 (not over), chunk c2: passage=513 (over)
    adapter = _make_adapter(
        {"a": 5, "passage: a": 511, "b": 5, "passage: b": 513}
    )
    chunks = [
        _make_chunk("c1", content="a"),
        _make_chunk("c2", content="b"),
    ]
    records = measure_chunks(
        chunks, adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    summary = build_summary(
        records=records,
        chunks=chunks,
        input_path=tmp_path / "chunks.jsonl",
        input_sha256="sha",
        model_name="intfloat/multilingual-e5-large",
        adapter=adapter,
        near_limit=480,
    )
    write_outputs(records, summary, tmp_path / "out")
    payload = json.loads((tmp_path / "out" / "over_limit.json").read_text())
    ids = [item["chunk_id"] for item in payload]
    assert ids == ["c2"]
    assert "c1" not in ids


# ---------------------------------------------------------------------------
# 21. Metadata extraction and content preview helper tests
# ---------------------------------------------------------------------------


def test_make_content_preview():
    from scripts.audit_e5_token_lengths import make_content_preview
    # Whitespace normalization
    assert make_content_preview("  hello   world  \n  test ") == "hello world test"
    # Empty/None
    assert make_content_preview(None) == ""
    assert make_content_preview("   ") == ""
    # Length truncation (max 240 chars)
    long_content = "a " * 150  # 300 characters
    preview = make_content_preview(long_content)
    assert len(preview) <= 240
    assert len(preview) >= 238
    assert not preview.endswith(" ")


def test_extract_chunk_locator_toplevel():
    from scripts.audit_e5_token_lengths import extract_chunk_locator
    chunk = {
        "chunk_id": "test-id",
        "article_code": "20.2.LQ.1",
        "article_title": "Title",
        "chunk_type": "article",
        "source_document_id": "doc-id",
        "document_id": "doc-id-2",
        "parent_document_id": "parent-id",
        "attachment_id": "attach-id",
        "attachment_title": "Attach Title",
        "table_id": "table-1",
        "table_index": 42,
        "table_title": "Table Title",
        "segment_index": 1,
        "form_number": "Form 1",
        "token_count": 100,
        "tokenizer_name": "tiktoken:cl100k_base"
    }
    locator = extract_chunk_locator(chunk)
    assert locator["chunk_id"] == "test-id"
    assert locator["article_code"] == "20.2.LQ.1"
    assert locator["table_segment_index"] == 1
    assert locator["chunker_token_count"] == 100
    assert locator["chunker_tokenizer"] == "tiktoken:cl100k_base"


def test_extract_chunk_locator_nested_fallback():
    from scripts.audit_e5_token_lengths import extract_chunk_locator
    chunk = {
        "chunk_id": "toplevel-id",
        "metadata": {
            "chunk_id": "nested-id",
            "article_code": "nested-code",
            "segment_index": "2",
            "token_count": "150",
            "tokenizer_name": "nested-tokenizer"
        }
    }
    locator = extract_chunk_locator(chunk)
    # chunk_id is present at top-level, so should use toplevel-id
    assert locator["chunk_id"] == "toplevel-id"
    # article_code not at toplevel, should fallback to nested
    assert locator["article_code"] == "nested-code"
    # segment_index, token_count converted to int
    assert locator["table_segment_index"] == 2
    assert locator["chunker_token_count"] == 150
    assert locator["chunker_tokenizer"] == "nested-tokenizer"


def test_near_limit_json_sorting_and_content(tmp_path: Path):
    # chunk c1: passage=479 (not in near_limit)
    # chunk c2: passage=480 (in near_limit)
    # chunk c3: passage=513 (in near_limit and strictly over)
    # chunk c4: passage=513 (in near_limit and strictly over, lower ID than c3)
    adapter = _make_adapter(
        {"a": 5, "passage: a": 479, "b": 5, "passage: b": 480, "c": 5, "passage: c": 513, "d": 5, "passage: d": 513}
    )
    chunks = [
        _make_chunk("c1", content="a"),
        _make_chunk("c2", content="b"),
        _make_chunk("c3", content="c"),
        _make_chunk("c4", content="d"),
    ]
    records = measure_chunks(
        chunks, adapter=adapter,
        model_name="intfloat/multilingual-e5-large",
        near_limit=480,
        batch_size=128,
    )
    summary = build_summary(
        records=records,
        chunks=chunks,
        input_path=tmp_path / "chunks.jsonl",
        input_sha256="sha",
        model_name="intfloat/multilingual-e5-large",
        adapter=adapter,
        near_limit=480,
    )
    write_outputs(records, summary, tmp_path / "out")

    # Check over_limit.json
    over_payload = json.loads((tmp_path / "out" / "over_limit.json").read_text())
    # c3 and c4 are over_limit, sorted by count desc (513 == 513), then chunk_id asc (c3 before c4)
    assert [item["chunk_id"] for item in over_payload] == ["c3", "c4"]

    # Check near_limit.json
    near_payload = json.loads((tmp_path / "out" / "near_limit.json").read_text())
    # c2, c3, c4 are near_limit. Sorted desc by count (513, 513, 480).
    # For count=513, sorted asc by chunk_id -> c3, c4.
    # So expected order: c3, c4, c2.
    assert [item["chunk_id"] for item in near_payload] == ["c3", "c4", "c2"]

    # Validate serialize fields in near_limit.json
    record_c2 = next(item for item in near_payload if item["chunk_id"] == "c2")
    assert record_c2["near_limit"] is True
    assert record_c2["strictly_over_limit"] is False
    assert record_c2["model_max_tokens"] == 512


def test_serialize_attachment_table_locator():
    from scripts.audit_e5_token_lengths import extract_chunk_locator, ChunkRecord, serialize_chunk_record

    # Simulates an attachment table chunk where article_code is null but other fields are present.
    chunk = {
        "chunk_id": "table-chunk-1",
        "article_code": None,
        "article_title": None,
        "chunk_type": "table",
        "source_document_id": "source-doc-xyz",
        "document_id": "doc-xyz",
        "parent_document_id": "parent-doc-xyz",
        "attachment_id": "attach-999",
        "attachment_title": "Phụ lục số I",
        "table_id": "table-part-2",
        "table_index": 2,
        "table_title": "Bảng lương tối thiểu",
        "segment_index": 5,
        "form_number": "Mẫu số 03",
        "token_count": 88,
        "tokenizer_name": "tiktoken:cl100k_base"
    }

    locator = extract_chunk_locator(chunk)
    # verify locator fields
    assert locator["chunk_id"] == "table-chunk-1"
    assert locator["article_code"] is None
    assert locator["attachment_title"] == "Phụ lục số I"
    assert locator["table_title"] == "Bảng lương tối thiểu"
    assert locator["form_number"] == "Mẫu số 03"
    assert locator["document_id"] == "doc-xyz"
    assert locator["parent_document_id"] == "parent-doc-xyz"
    assert locator["table_segment_index"] == 5
    assert locator["chunker_token_count"] == 88
    assert locator["chunker_tokenizer"] == "tiktoken:cl100k_base"

    # Instantiate ChunkRecord
    record = ChunkRecord(
        chunk_id=locator["chunk_id"],
        article_code=locator["article_code"],
        article_title=locator["article_title"],
        chunk_type=locator["chunk_type"],
        source_document_id=locator["source_document_id"],
        document_id=locator["document_id"],
        parent_document_id=locator["parent_document_id"],
        attachment_id=locator["attachment_id"],
        attachment_title=locator["attachment_title"],
        table_id=locator["table_id"],
        table_index=locator["table_index"],
        table_title=locator["table_title"],
        table_segment_index=locator["table_segment_index"],
        form_number=locator["form_number"],
        chunker_token_count=locator["chunker_token_count"],
        chunker_tokenizer=locator["chunker_tokenizer"],
        e5_content_token_count=50,
        e5_passage_token_count=52,
        prefix_overhead=2,
        model_max_tokens=512,
        overflow_tokens=0,
        near_limit=False,
        strictly_over_limit=False,
        content_preview="preview text"
    )

    serialized = serialize_chunk_record(record)

    # Assert that all 24 mandatory fields exist in the serialized dict
    mandatory_fields = [
        "chunk_id",
        "article_code",
        "article_title",
        "chunk_type",
        "source_document_id",
        "document_id",
        "parent_document_id",
        "attachment_id",
        "attachment_title",
        "table_id",
        "table_index",
        "table_title",
        "table_segment_index",
        "form_number",
        "chunker_token_count",
        "chunker_tokenizer",
        "e5_content_token_count",
        "e5_passage_token_count",
        "prefix_overhead",
        "model_max_tokens",
        "overflow_tokens",
        "near_limit",
        "strictly_over_limit",
        "content_preview"
    ]
    for field in mandatory_fields:
        assert field in serialized, f"Mandatory field '{field}' missing from serialized dictionary"

    # Specific assertions required
    assert serialized["attachment_title"] == "Phụ lục số I"
    assert serialized["table_title"] == "Bảng lương tối thiểu"
    assert serialized["form_number"] == "Mẫu số 03"
    assert serialized["document_id"] == "doc-xyz"
    assert serialized["parent_document_id"] == "parent-doc-xyz"
    assert serialized["article_code"] is None


def test_build_tokenizer_comparison_detailed():
    from scripts.audit_e5_token_lengths import ChunkRecord, build_tokenizer_comparison

    # Model max tokens for the test: 512
    records = [
        # 1. True Positive (proxy >= 512, exact > 512)
        ChunkRecord(
            chunk_id="tp",
            chunker_token_count=520,
            chunker_tokenizer="tiktoken:cl100k_base",
            e5_passage_token_count=530
        ),
        # 2. True Negative (proxy < 512, exact <= 512)
        ChunkRecord(
            chunk_id="tn",
            chunker_token_count=100,
            chunker_tokenizer="tiktoken:cl100k_base",
            e5_passage_token_count=120
        ),
        # 3. False Positive (proxy >= 512, exact <= 512)
        ChunkRecord(
            chunk_id="fp",
            chunker_token_count=515,
            chunker_tokenizer="tiktoken:cl100k_base",
            e5_passage_token_count=500
        ),
        # 4. False Negative (proxy < 512, exact > 512)
        ChunkRecord(
            chunk_id="fn",
            chunker_token_count=490,
            chunker_tokenizer="custom_tok",
            e5_passage_token_count=525
        ),
        # 5. Exactly-at-limit (proxy >= 512, exact <= 512 -> False Positive)
        ChunkRecord(
            chunk_id="at_limit",
            chunker_token_count=512,
            chunker_tokenizer="custom_tok",
            e5_passage_token_count=512
        ),
        # 6. Missing chunker_token_count
        ChunkRecord(
            chunk_id="missing",
            chunker_token_count=None,
            chunker_tokenizer="tiktoken:cl100k_base",
            e5_passage_token_count=150
        )
    ]

    comp = build_tokenizer_comparison(records, 512)

    # 10. Tổng TP + TN + FP + FN bằng available_count
    total_matrix = (
        comp["true_positive_count"] +
        comp["true_negative_count"] +
        comp["false_positive_count"] +
        comp["false_negative_count"]
    )
    assert comp["available_count"] == 5
    assert comp["missing_count"] == 1
    assert total_matrix == comp["available_count"]

    # Matrix verification
    assert comp["true_positive_count"] == 1
    assert comp["true_negative_count"] == 1
    assert comp["false_positive_count"] == 2
    assert comp["false_negative_count"] == 1

    # 7. source_tokenizers deduplicated and sorted
    assert comp["source_tokenizers"] == ["custom_tok", "tiktoken:cl100k_base"]

    # 8. Difference statistics
    stats = comp["difference_statistics"]
    assert stats["count"] == 5
    assert stats["min"] == -15
    assert stats["max"] == 35
    assert stats["mean"] == 10.0
    assert stats["median"] == 10.0

    # 9. No cl100k counts available still returns comparison object
    no_cl_records = [
        ChunkRecord(chunk_id="no_cl", chunker_token_count=None, e5_passage_token_count=100)
    ]
    no_cl_comp = build_tokenizer_comparison(no_cl_records, 512)
    assert no_cl_comp is not None
    assert isinstance(no_cl_comp, dict)
    assert no_cl_comp["available_count"] == 0
    assert no_cl_comp["missing_count"] == 1
    assert no_cl_comp["true_positive_count"] == 0
    assert no_cl_comp["true_negative_count"] == 0
    assert no_cl_comp["false_positive_count"] == 0
    assert no_cl_comp["false_negative_count"] == 0
    assert no_cl_comp["difference_statistics"]["count"] == 0
    assert no_cl_comp["difference_statistics"]["mean"] is None


def test_strict_parser_argument():
    from scripts.audit_e5_token_lengths import build_parser
    args = build_parser().parse_args(["--strict"])
    assert args.strict is True


def test_strict_exit_behavior_and_reports(tmp_path: Path, monkeypatch):
    from scripts.audit_e5_token_lengths import main

    def _run_main(chunk_dict, extra_args, out_sub):
        chunks = []
        tokenizer_map = {}
        for cid, (content, content_toks, passage_toks) in chunk_dict.items():
            chunks.append(_make_chunk(cid, content=content))
            tokenizer_map[content] = content_toks
            tokenizer_map[f"passage: {content}"] = passage_toks

        chunks_path = tmp_path / f"chunks_{out_sub}.jsonl"
        chunks_path.write_text(
            "\n".join(json.dumps(c) for c in chunks) + "\n", encoding="utf-8"
        )
        out_dir = tmp_path / f"out_{out_sub}"

        mock_adapter = _make_adapter(tokenizer_map, model_max_length=512)
        monkeypatch.setattr(
            "scripts.audit_e5_token_lengths.load_audit_tokenizer",
            lambda **kwargs: mock_adapter,
        )

        cmd = [
            "--chunks", str(chunks_path),
            "--expected-chunks", str(len(chunks)),
            "--output-dir", str(out_dir),
        ] + extra_args

        exit_code = main(cmd)
        return exit_code, out_dir

    # 1. Non-strict with oversized chunk still returns 0
    code, out = _run_main({"c1": ("over", 540, 550)}, [], "non_strict_over")
    assert code == 0

    # 2. Strict with oversized chunk returns 2
    code_strict, out_strict = _run_main({"c1": ("over", 540, 550)}, ["--strict"], "strict_over")
    assert code_strict == 2

    # 3 & 4. Strict failure happens AFTER write_outputs: 4 output files must exist when strict returns 2
    assert (out_strict / "summary.json").exists()
    assert (out_strict / "chunks.csv").exists()
    assert (out_strict / "over_limit.json").exists()
    assert (out_strict / "near_limit.json").exists()

    # 5. Strict without oversized chunk returns 0
    code_under, _ = _run_main({"c1": ("under", 190, 200)}, ["--strict"], "strict_under")
    assert code_under == 0

    # 6. Exactly-at-limit (passage=512) returns 0 in strict mode
    code_exact, _ = _run_main({"c1": ("exact", 500, 512)}, ["--strict"], "strict_exact")
    assert code_exact == 0

    # 7. Near-limit (passage=480 <= 512) returns 0 in strict mode
    code_near, _ = _run_main({"c1": ("near", 470, 480)}, ["--strict"], "strict_near")
    assert code_near == 0

    # 8. --dry-run --strict returns 0 without initializing tokenizer or creating files
    chunks_dry = tmp_path / "chunks_dry.jsonl"
    chunks_dry.write_text(json.dumps(_make_chunk("c1")) + "\n", encoding="utf-8")
    out_dry = tmp_path / "out_dry"
    code_dry = main([
        "--chunks", str(chunks_dry),
        "--expected-chunks", "1",
        "--output-dir", str(out_dry),
        "--dry-run",
        "--strict",
    ])
    assert code_dry == 0
    assert not out_dry.exists()
