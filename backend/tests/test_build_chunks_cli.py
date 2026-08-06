"""Tests for scripts/build_chunks.py — Prompt 4C."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CORPUS_PATH = Path("data/processed/articles_raw.json")


def _simple_corpus() -> dict:
    """Minimal valid corpus matching canonical article schema for fast unit tests."""
    return {
        "metadata": {
            "document_id": "test_doc",
            "topic_code": "LD",
            "topic_name": "Lao Dong",
            "parser_version": "1.0.0",
            "source_sha256": "abc123",
        },
        "articles": [
            {
                "article_id": "art_1",
                "article_code": "LQ.1",
                "codification_code": "20.2.LQ.1",
                "article_title": "Quy định chung",
                "topic_code": "20.2",
                "topic_name": "Lao động",
                "chapter": {"number": "I", "title": "Chương Một", "anchor_id": "chap_1"},
                "section": {"number": "1", "title": "Mục Một", "anchor_id": "sec_1"},
                "source_type": "LQ",
                "source_document_id": "test_doc",
                "source_note_text": None,
                "source_urls": [],
                "parser_version": "1.0.0",
                "source_sha256": "abc123",
                # relations must be a list[dict], NOT a dict with nested keys
                "relations": [],
                "attachments": [],
                "tables": [],
                "content_units": [
                    {
                        "unit_id": "unit_1",
                        "unit_type": "preamble",
                        "text": "Nội dung thử nghiệm.",
                    }
                ],
            }
        ],
    }



def _run_cli(argv: list[str], expect_exit: int = 0) -> None:
    from scripts.build_chunks import main

    code = main(argv)
    assert code == expect_exit, f"Expected exit {expect_exit}, got {code}"


def _write_corpus(path: Path, corpus: dict | None = None) -> None:
    corpus = corpus or _simple_corpus()
    path.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Import does not create files
# ---------------------------------------------------------------------------

def test_import_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import importlib, scripts.build_chunks  # noqa: E401
    importlib.reload(scripts.build_chunks)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# 2. --help exits 0
# ---------------------------------------------------------------------------

def test_help_exits_zero():
    from scripts.build_chunks import main

    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# 3. build_parser defaults
# ---------------------------------------------------------------------------

def test_build_parser_defaults():
    from scripts.build_chunks import build_parser

    args = build_parser().parse_args([])
    assert args.input == "data/processed/articles_raw.json"
    assert args.output == "data/processed/legal_chunks.jsonl"
    assert args.summary_output == "data/processed/chunking_summary.json"
    assert args.target_tokens == 500
    assert args.max_tokens == 750
    assert args.overlap == 80
    assert args.tokenizer == "cl100k_base"
    assert args.strict is False
    assert args.log_level == "INFO"


# ---------------------------------------------------------------------------
# 4. Successful run (tmp_path)
# ---------------------------------------------------------------------------

def test_successful_run(tmp_path):
    inp = tmp_path / "corpus.json"
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _write_corpus(inp)
    _run_cli([
        "--input", str(inp),
        "--output", str(out),
        "--summary-output", str(summ),
        "--tokenizer", "regex-estimate-v1",
        "--log-level", "WARNING",
    ])
    assert out.is_file()
    assert summ.is_file()


# ---------------------------------------------------------------------------
# 5. Strict mode success
# ---------------------------------------------------------------------------

def test_strict_success(tmp_path):
    inp = tmp_path / "corpus.json"
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _write_corpus(inp)
    _run_cli([
        "--input", str(inp),
        "--output", str(out),
        "--summary-output", str(summ),
        "--tokenizer", "regex-estimate-v1",
        "--strict",
        "--log-level", "WARNING",
    ])
    assert out.is_file()
    assert summ.is_file()


# ---------------------------------------------------------------------------
# 6. JSONL every line parses
# ---------------------------------------------------------------------------

def test_jsonl_every_line_parses(tmp_path):
    inp = tmp_path / "corpus.json"
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _write_corpus(inp)
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ), "--tokenizer", "regex-estimate-v1",
              "--log-level", "WARNING"])
    lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    for line in lines:
        obj = json.loads(line)
        assert isinstance(obj, dict)


# ---------------------------------------------------------------------------
# 7. JSONL line count == summary chunk_count
# ---------------------------------------------------------------------------

def test_jsonl_line_count_matches_summary(tmp_path):
    inp = tmp_path / "corpus.json"
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _write_corpus(inp)
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ), "--tokenizer", "regex-estimate-v1",
              "--log-level", "WARNING"])
    lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    summary = json.loads(summ.read_text(encoding="utf-8"))
    assert len(lines) == summary["chunk_count"]


# ---------------------------------------------------------------------------
# 8. Summary parses
# ---------------------------------------------------------------------------

def test_summary_parses(tmp_path):
    inp = tmp_path / "corpus.json"
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _write_corpus(inp)
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ), "--tokenizer", "regex-estimate-v1",
              "--log-level", "WARNING"])
    obj = json.loads(summ.read_text(encoding="utf-8"))
    assert isinstance(obj, dict)


# ---------------------------------------------------------------------------
# 9. Summary validation is_valid true
# ---------------------------------------------------------------------------

def test_summary_validation_is_valid(tmp_path):
    inp = tmp_path / "corpus.json"
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _write_corpus(inp)
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ), "--tokenizer", "regex-estimate-v1",
              "--log-level", "WARNING"])
    obj = json.loads(summ.read_text(encoding="utf-8"))
    assert obj["validation"]["is_valid"] is True


# ---------------------------------------------------------------------------
# 10. JSONL trailing newline
# ---------------------------------------------------------------------------

def test_jsonl_trailing_newline(tmp_path):
    inp = tmp_path / "corpus.json"
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _write_corpus(inp)
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ), "--tokenizer", "regex-estimate-v1",
              "--log-level", "WARNING"])
    assert out.read_text(encoding="utf-8").endswith("\n")


# ---------------------------------------------------------------------------
# 11. Summary trailing newline
# ---------------------------------------------------------------------------

def test_summary_trailing_newline(tmp_path):
    inp = tmp_path / "corpus.json"
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _write_corpus(inp)
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ), "--tokenizer", "regex-estimate-v1",
              "--log-level", "WARNING"])
    assert summ.read_text(encoding="utf-8").endswith("\n")


# ---------------------------------------------------------------------------
# 12. Invalid input path → non-zero
# ---------------------------------------------------------------------------

def test_invalid_input_path(tmp_path):
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(tmp_path / "no_such_file.json"),
              "--output", str(out), "--summary-output", str(summ)], expect_exit=2)
    assert not out.exists()


# ---------------------------------------------------------------------------
# 13. Invalid JSON → non-zero
# ---------------------------------------------------------------------------

def test_invalid_json(tmp_path):
    inp = tmp_path / "bad.json"
    inp.write_text("NOT JSON", encoding="utf-8")
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ)], expect_exit=2)
    assert not out.exists()


# ---------------------------------------------------------------------------
# 14. Top-level not dict → non-zero
# ---------------------------------------------------------------------------

def test_top_level_not_dict(tmp_path):
    inp = tmp_path / "array.json"
    inp.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ)], expect_exit=2)


# ---------------------------------------------------------------------------
# 15. Metadata missing / wrong type → non-zero
# ---------------------------------------------------------------------------

def test_metadata_missing(tmp_path):
    corpus = _simple_corpus()
    del corpus["metadata"]
    inp = tmp_path / "c.json"
    inp.write_text(json.dumps(corpus), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ)], expect_exit=2)


def test_metadata_wrong_type(tmp_path):
    corpus = _simple_corpus()
    corpus["metadata"] = "string"
    inp = tmp_path / "c.json"
    inp.write_text(json.dumps(corpus), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ)], expect_exit=2)


# ---------------------------------------------------------------------------
# 16. Articles missing / wrong type → non-zero
# ---------------------------------------------------------------------------

def test_articles_missing(tmp_path):
    corpus = _simple_corpus()
    del corpus["articles"]
    inp = tmp_path / "c.json"
    inp.write_text(json.dumps(corpus), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ)], expect_exit=2)


def test_articles_wrong_type(tmp_path):
    corpus = _simple_corpus()
    corpus["articles"] = "not-a-list"
    inp = tmp_path / "c.json"
    inp.write_text(json.dumps(corpus), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ)], expect_exit=2)


# ---------------------------------------------------------------------------
# 17. Invalid tokenizer → non-zero, no output
# ---------------------------------------------------------------------------

def test_invalid_tokenizer(tmp_path):
    inp = tmp_path / "corpus.json"
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _write_corpus(inp)
    _run_cli(
        ["--input", str(inp), "--output", str(out),
         "--summary-output", str(summ), "--tokenizer", "unknown-tok",
         "--log-level", "ERROR"],
        expect_exit=2,
    )
    assert not out.exists()



# ---------------------------------------------------------------------------
# 18. Output == input → non-zero
# ---------------------------------------------------------------------------

def test_output_same_as_input(tmp_path):
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(inp),
              "--summary-output", str(summ)], expect_exit=2)


# ---------------------------------------------------------------------------
# 19. Summary == input → non-zero
# ---------------------------------------------------------------------------

def test_summary_same_as_input(tmp_path):
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(inp)], expect_exit=2)


# ---------------------------------------------------------------------------
# 20. Output == summary → non-zero
# ---------------------------------------------------------------------------

def test_output_same_as_summary(tmp_path):
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    shared = tmp_path / "same.json"
    _run_cli(["--input", str(inp), "--output", str(shared),
              "--summary-output", str(shared)], expect_exit=2)


# ---------------------------------------------------------------------------
# 21 & 22. Strict failure preserves old output + summary
# ---------------------------------------------------------------------------

def test_strict_failure_preserves_old_output(tmp_path, monkeypatch):
    from scripts import build_chunks

    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    out.write_text("OLD_OUTPUT", encoding="utf-8")
    summ.write_text("OLD_SUMMARY", encoding="utf-8")

    bad_validation = {"is_valid": False, "errors": ["err1"], "warnings": []}

    with monkeypatch.context() as m:
        m.setattr(build_chunks, "validate_chunks",
                  lambda *a, **kw: bad_validation, raising=False)

        import importlib
        importlib.reload(build_chunks)

        # Patch via module attribute after reload
        from scripts import build_chunks as bc
        original_validate = None

        # Use a different approach: monkeypatch the imported name in the module
        with mock.patch("scripts.build_chunks.validate_chunks",
                        return_value=bad_validation):
            code = bc.main([
                "--input", str(inp), "--output", str(out),
                "--summary-output", str(summ),
                "--tokenizer", "regex-estimate-v1",
                "--strict", "--log-level", "ERROR",
            ])

    assert code != 0
    assert out.read_text(encoding="utf-8") == "OLD_OUTPUT"
    assert summ.read_text(encoding="utf-8") == "OLD_SUMMARY"


def test_strict_failure_preserves_old_summary(tmp_path):
    """Summary is also preserved on strict failure."""
    from scripts import build_chunks as bc

    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    summ.write_text("OLD_SUMMARY", encoding="utf-8")

    bad_validation = {"is_valid": False, "errors": ["e1"], "warnings": []}
    with mock.patch("scripts.build_chunks.validate_chunks",
                    return_value=bad_validation):
        code = bc.main([
            "--input", str(inp), "--output", str(out),
            "--summary-output", str(summ),
            "--tokenizer", "regex-estimate-v1",
            "--strict", "--log-level", "ERROR",
        ])
    assert code != 0
    assert summ.read_text(encoding="utf-8") == "OLD_SUMMARY"


# ---------------------------------------------------------------------------
# 23. Temp files cleaned on error
# ---------------------------------------------------------------------------

def test_temp_files_cleaned_on_write_error(tmp_path):
    """If os.replace raises, the .tmp file must be removed."""
    from scripts import build_chunks as bc

    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"

    with mock.patch("os.replace", side_effect=OSError("disk full")):
        code = bc.main([
            "--input", str(inp), "--output", str(out),
            "--summary-output", str(summ),
            "--tokenizer", "regex-estimate-v1",
            "--log-level", "ERROR",
        ])
    assert code != 0
    tmp_files = list(tmp_path.glob(".tmp_*"))
    assert tmp_files == [], f"Leftover temp files: {tmp_files}"


# ---------------------------------------------------------------------------
# 24. Parent directories auto-created
# ---------------------------------------------------------------------------

def test_parent_directories_created(tmp_path):
    inp = tmp_path / "corpus.json"
    out = tmp_path / "deep" / "nested" / "out.jsonl"
    summ = tmp_path / "deep2" / "summ.json"
    _write_corpus(inp)
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])
    assert out.is_file()
    assert summ.is_file()


# ---------------------------------------------------------------------------
# 25 & 26. JSONL and summary byte-for-byte deterministic
# ---------------------------------------------------------------------------

def test_jsonl_deterministic(tmp_path):
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out1 = tmp_path / "run1.jsonl"
    summ1 = tmp_path / "summ1.json"
    out2 = tmp_path / "run2.jsonl"
    summ2 = tmp_path / "summ2.json"

    _run_cli(["--input", str(inp), "--output", str(out1),
              "--summary-output", str(summ1),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])
    _run_cli(["--input", str(inp), "--output", str(out2),
              "--summary-output", str(summ2),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])

    assert out1.read_bytes() == out2.read_bytes()


def test_summary_deterministic(tmp_path):
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out1 = tmp_path / "run1.jsonl"
    summ1 = tmp_path / "summ1.json"
    out2 = tmp_path / "run2.jsonl"
    summ2 = tmp_path / "summ2.json"

    _run_cli(["--input", str(inp), "--output", str(out1),
              "--summary-output", str(summ1),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])
    _run_cli(["--input", str(inp), "--output", str(out2),
              "--summary-output", str(summ2),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])

    assert summ1.read_bytes() == summ2.read_bytes()


# ---------------------------------------------------------------------------
# 27. CLI does not mutate input corpus
# ---------------------------------------------------------------------------

def test_cli_does_not_mutate_input(tmp_path):
    inp = tmp_path / "corpus.json"
    corpus = _simple_corpus()
    _write_corpus(inp, corpus)
    original = copy.deepcopy(corpus)

    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])

    reread = json.loads(inp.read_text(encoding="utf-8"))
    assert reread == original


# ---------------------------------------------------------------------------
# 28. regex-estimate-v1 tokenizer works
# ---------------------------------------------------------------------------

def test_regex_tokenizer(tmp_path):
    inp = tmp_path / "corpus.json"
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _write_corpus(inp)
    _run_cli(["--input", str(inp), "--output", str(out),
              "--summary-output", str(summ),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])
    obj = json.loads(summ.read_text(encoding="utf-8"))
    assert obj["tokenizer_name"] == "regex-estimate-v1"


# ---------------------------------------------------------------------------
# 29. Non-strict writes even with validation fail
# ---------------------------------------------------------------------------

def test_non_strict_writes_on_validation_fail(tmp_path):
    from scripts import build_chunks as bc

    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"

    bad_validation = {
        "is_valid": False,
        "errors": ["some_error: x"],
        "warnings": [],
        "article_count": 1,
        "chunk_count": 1,
        "covered_article_count": 0,
        "missing_article_ids": ["art_1"],
        "duplicate_chunk_id_count": 0,
        "duplicate_chunk_ids": [],
        "duplicate_chunk_key_count": 0,
        "duplicate_chunk_keys": [],
        "empty_content_count": 0,
        "empty_content_chunk_ids": [],
        "empty_body_count": 0,
        "empty_body_chunk_ids": [],
        "invalid_parent_count": 0,
        "invalid_parent_chunk_ids": [],
        "invalid_parent_article_ids": [],
        "missing_required_field_count": 0,
        "chunks_missing_required_fields": [],
        "invalid_chunk_type_count": 0,
        "invalid_chunk_type_chunks": [],
        "invalid_unit_type_count": 0,
        "invalid_unit_type_chunks": [],
        "token_count_mismatch_count": 0,
        "token_count_mismatch_chunks": [],
        "tokenizer_name_mismatch_count": 0,
        "tokenizer_name_mismatch_chunks": [],
        "oversized_chunk_count": 0,
        "oversized_chunks": [],
        "max_token_count": 0,
        "requires_fallback_count": 0,
        "fallback_pending_chunks": [],
        "stable_id_check": True,
        "unstable_id_chunks": [],
        "JSON_serialization_error_count": 0,
        "JSON_serialization_error_chunks": [],
        "relation_list_length_mismatch_count": 0,
        "relation_list_length_mismatch_chunks": [],
        "canonical_non_table_unit_count": 1,
        "covered_non_table_unit_count": 1,
        "missing_non_table_unit_count": 0,
        "missing_non_table_unit_ids": [],
        "unknown_source_unit_count": 0,
        "unknown_source_unit_ids": [],
        "canonical_table_count": 0,
        "covered_table_count": 0,
        "missing_table_count": 0,
        "missing_table_ids": [],
        "unknown_table_count": 0,
        "unknown_table_ids": [],
        "duplicate_table_segment_key_count": 0,
        "duplicate_table_segment_keys": [],
        "invalid_table_metadata_count": 0,
        "invalid_table_metadata_chunks": [],
        "table_segment_sequence_error_count": 0,
        "table_segment_sequence_errors": [],
        "table_ordering_error_count": 0,
        "table_ordering_error_articles": [],
        "input_chunk_count_preserved": True,
        "input_mutated": False,
    }

    with mock.patch("scripts.build_chunks.validate_chunks",
                    return_value=bad_validation):
        code = bc.main([
            "--input", str(inp), "--output", str(out),
            "--summary-output", str(summ),
            "--tokenizer", "regex-estimate-v1",
            "--log-level", "ERROR",
        ])

    assert code == 0
    assert out.is_file()
    assert summ.is_file()
    summary_obj = json.loads(summ.read_text(encoding="utf-8"))
    assert summary_obj["validation"]["is_valid"] is False


# ---------------------------------------------------------------------------
# 30. main() returns int exit code
# ---------------------------------------------------------------------------

def test_main_returns_int(tmp_path):
    from scripts.build_chunks import main

    inp = tmp_path / "corpus.json"
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _write_corpus(inp)
    code = main([
        "--input", str(inp), "--output", str(out),
        "--summary-output", str(summ),
        "--tokenizer", "regex-estimate-v1",
        "--log-level", "WARNING",
    ])
    assert isinstance(code, int)
    assert code == 0


# ---------------------------------------------------------------------------
# Full corpus tests (skipped if corpus not present)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not CORPUS_PATH.is_file(), reason="articles_raw.json not found")
def test_real_corpus_cli(tmp_path):
    out = tmp_path / "real.jsonl"
    summ = tmp_path / "real_summ.json"
    _run_cli([
        "--input", str(CORPUS_PATH),
        "--output", str(out),
        "--summary-output", str(summ),
        "--strict",
        "--log-level", "WARNING",
    ])
    obj = json.loads(summ.read_text(encoding="utf-8"))
    assert obj["article_count"] == 477
    assert obj["validation"]["is_valid"] is True

    lines = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert obj["chunk_count"] == len(lines)
    assert len(lines) > 0


@pytest.mark.skipif(not CORPUS_PATH.is_file(), reason="articles_raw.json not found")
def test_real_corpus_cli_deterministic(tmp_path):
    out1 = tmp_path / "run1.jsonl"
    summ1 = tmp_path / "summ1.json"
    out2 = tmp_path / "run2.jsonl"
    summ2 = tmp_path / "summ2.json"
    for out, summ in [(out1, summ1), (out2, summ2)]:
        _run_cli([
            "--input", str(CORPUS_PATH),
            "--output", str(out),
            "--summary-output", str(summ),
            "--log-level", "WARNING",
        ])
    assert out1.read_bytes() == out2.read_bytes()
    assert summ1.read_bytes() == summ2.read_bytes()


# ---------------------------------------------------------------------------
# Atomic write unit tests (Prompt 4C2A)
# ---------------------------------------------------------------------------

def test_atomic_write_creates_parent_directory(tmp_path):
    """_atomic_write_text must create parent directories automatically."""
    from scripts.build_chunks import _atomic_write_text

    target = tmp_path / "a" / "b" / "c" / "file.txt"
    assert not target.parent.exists()
    _atomic_write_text(target, "hello")
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_replaces_existing_file(tmp_path):
    """_atomic_write_text must atomically overwrite an existing file."""
    from scripts.build_chunks import _atomic_write_text

    target = tmp_path / "data.txt"
    target.write_text("OLD", encoding="utf-8")
    _atomic_write_text(target, "NEW")
    assert target.read_text(encoding="utf-8") == "NEW"


def test_atomic_write_has_exact_content(tmp_path):
    """File must contain exactly the text passed in, including Unicode."""
    from scripts.build_chunks import _atomic_write_text

    target = tmp_path / "out.txt"
    text = "Người lao động\n有效期限\n"
    _atomic_write_text(target, text)
    assert target.read_text(encoding="utf-8") == text


def test_atomic_write_leaves_no_temp_after_success(tmp_path):
    """No .tmp* files must remain after a successful write."""
    from scripts.build_chunks import _atomic_write_text

    target = tmp_path / "file.jsonl"
    _atomic_write_text(target, "content")
    leftover = list(tmp_path.glob(".tmp_*"))
    assert leftover == [], f"Leftover temp files after success: {leftover}"


def test_atomic_write_cleans_temp_after_failure(tmp_path):
    """If os.replace raises, any temp file must be cleaned up and the exception re-raised."""
    from scripts.build_chunks import _atomic_write_text

    target = tmp_path / "file.jsonl"
    with mock.patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            _atomic_write_text(target, "content")

    leftover = list(tmp_path.glob(".tmp_*"))
    assert leftover == [], f"Leftover temp files after failure: {leftover}"
    # Destination must not have been created
    assert not target.exists()


def test_cli_creates_nested_output_directories(tmp_path):
    """CLI must create deeply nested output directories on its own."""
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "lvl1" / "lvl2" / "lvl3" / "out.jsonl"
    summ = tmp_path / "summaries" / "nested" / "summ.json"
    _run_cli([
        "--input", str(inp),
        "--output", str(out),
        "--summary-output", str(summ),
        "--tokenizer", "regex-estimate-v1",
        "--log-level", "WARNING",
    ])
    assert out.is_file()
    assert summ.is_file()


def test_cli_write_failure_returns_one(tmp_path):
    """If the atomic write fails, CLI must return exit code 1."""
    from scripts import build_chunks as bc

    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"

    with mock.patch("os.replace", side_effect=OSError("no space left")):
        code = bc.main([
            "--input", str(inp),
            "--output", str(out),
            "--summary-output", str(summ),
            "--tokenizer", "regex-estimate-v1",
            "--log-level", "ERROR",
        ])
    assert code == 1
    # Destination must not exist (atomic write was aborted)
    assert not out.exists()
    # No temp files left
    assert list(tmp_path.glob(".tmp_*")) == []


# ---------------------------------------------------------------------------
# Strict / non-strict validation failure tests (Prompt 4C2B)
# ---------------------------------------------------------------------------

def _bad_validation(errors: list[str] | None = None, warnings: list[str] | None = None) -> dict:
    """Return a minimal but fully-keyed validation dict where is_valid=False."""
    errors = errors if errors is not None else ["err_1", "err_2"]
    warnings = warnings if warnings is not None else []
    return {
        "is_valid": False,
        "errors": errors,
        "warnings": warnings,
        "article_count": 1,
        "chunk_count": 1,
        "covered_article_count": 0,
        "missing_article_ids": ["art_1"],
        "duplicate_chunk_id_count": 0,
        "duplicate_chunk_ids": [],
        "duplicate_chunk_key_count": 0,
        "duplicate_chunk_keys": [],
        "empty_content_count": 0,
        "empty_content_chunk_ids": [],
        "empty_body_count": 0,
        "empty_body_chunk_ids": [],
        "invalid_parent_count": 0,
        "invalid_parent_chunk_ids": [],
        "invalid_parent_article_ids": [],
        "missing_required_field_count": 0,
        "chunks_missing_required_fields": [],
        "invalid_chunk_type_count": 0,
        "invalid_chunk_type_chunks": [],
        "invalid_unit_type_count": 0,
        "invalid_unit_type_chunks": [],
        "token_count_mismatch_count": 0,
        "token_count_mismatch_chunks": [],
        "tokenizer_name_mismatch_count": 0,
        "tokenizer_name_mismatch_chunks": [],
        "oversized_chunk_count": 0,
        "oversized_chunks": [],
        "max_token_count": 0,
        "requires_fallback_count": 0,
        "fallback_pending_chunks": [],
        "stable_id_check": True,
        "unstable_id_chunks": [],
        "JSON_serialization_error_count": 0,
        "JSON_serialization_error_chunks": [],
        "relation_list_length_mismatch_count": 0,
        "relation_list_length_mismatch_chunks": [],
        "canonical_non_table_unit_count": 1,
        "covered_non_table_unit_count": 1,
        "missing_non_table_unit_count": 0,
        "missing_non_table_unit_ids": [],
        "unknown_source_unit_count": 0,
        "unknown_source_unit_ids": [],
        "canonical_table_count": 0,
        "covered_table_count": 0,
        "missing_table_count": 0,
        "missing_table_ids": [],
        "unknown_table_count": 0,
        "unknown_table_ids": [],
        "duplicate_table_segment_key_count": 0,
        "duplicate_table_segment_keys": [],
        "invalid_table_metadata_count": 0,
        "invalid_table_metadata_chunks": [],
        "table_segment_sequence_error_count": 0,
        "table_segment_sequence_errors": [],
        "table_ordering_error_count": 0,
        "table_ordering_error_articles": [],
        "input_chunk_count_preserved": True,
        "input_mutated": False,
    }


def _common_strict_argv(inp, out, summ):
    return [
        "--input", str(inp), "--output", str(out),
        "--summary-output", str(summ),
        "--tokenizer", "regex-estimate-v1",
        "--strict", "--log-level", "ERROR",
    ]


def _common_non_strict_argv(inp, out, summ):
    return [
        "--input", str(inp), "--output", str(out),
        "--summary-output", str(summ),
        "--tokenizer", "regex-estimate-v1",
        "--log-level", "ERROR",
    ]


# 1. Strict failure → exit 1
def test_strict_validation_failure_returns_one(tmp_path):
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    with mock.patch("scripts.build_chunks.validate_chunks", return_value=_bad_validation()):
        code = bc.main(_common_strict_argv(inp, out, summ))
    assert code == 1


# 2. Strict failure → old output preserved
def test_strict_failure_preserves_old_output(tmp_path):
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    out.write_text("OLD_OUTPUT", encoding="utf-8")
    summ.write_text("OLD_SUMMARY", encoding="utf-8")
    with mock.patch("scripts.build_chunks.validate_chunks", return_value=_bad_validation()):
        bc.main(_common_strict_argv(inp, out, summ))
    assert out.read_text(encoding="utf-8") == "OLD_OUTPUT"


# 3. Strict failure → old summary preserved
def test_strict_failure_preserves_old_summary(tmp_path):
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    out.write_text("OLD_OUTPUT", encoding="utf-8")
    summ.write_text("OLD_SUMMARY", encoding="utf-8")
    with mock.patch("scripts.build_chunks.validate_chunks", return_value=_bad_validation()):
        bc.main(_common_strict_argv(inp, out, summ))
    assert summ.read_text(encoding="utf-8") == "OLD_SUMMARY"


# 4. Strict failure → _atomic_write_text never called
def test_strict_failure_does_not_call_atomic_write(tmp_path):
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    with mock.patch("scripts.build_chunks.validate_chunks", return_value=_bad_validation()):
        with mock.patch("scripts.build_chunks._atomic_write_text") as mock_write:
            bc.main(_common_strict_argv(inp, out, summ))
    mock_write.assert_not_called()


# 5. Strict failure → no temp files
def test_strict_failure_leaves_no_temp_files(tmp_path):
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    with mock.patch("scripts.build_chunks.validate_chunks", return_value=_bad_validation()):
        bc.main(_common_strict_argv(inp, out, summ))
    assert list(tmp_path.glob(".tmp_*")) == []


# 6. Strict failure → error count logged
def test_strict_failure_logs_error_count(tmp_path, caplog):
    import logging
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    with caplog.at_level(logging.WARNING, logger="scripts.build_chunks"):
        with mock.patch("scripts.build_chunks.validate_chunks",
                        return_value=_bad_validation(errors=["e1", "e2", "e3"])):
            bc.main(_common_strict_argv(inp, out, summ))
    joined = "\n".join(caplog.messages)
    assert "3" in joined  # error count appears in log


# 7. Strict failure → at most 5 errors logged
def test_strict_failure_logs_at_most_five_errors(tmp_path, caplog):
    import logging
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    many_errors = [f"err_{i}" for i in range(10)]
    with caplog.at_level(logging.WARNING, logger="scripts.build_chunks"):
        with mock.patch("scripts.build_chunks.validate_chunks",
                        return_value=_bad_validation(errors=many_errors)):
            bc.main(_common_strict_argv(inp, out, summ))
    # Count how many individual error messages appear (err_0 … err_9)
    logged_errs = [m for m in caplog.messages if m.strip().startswith("err_")]
    assert len(logged_errs) <= 5


# 8. Non-strict validation failure → exit 0
def test_non_strict_validation_failure_returns_zero(tmp_path):
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    with mock.patch("scripts.build_chunks.validate_chunks", return_value=_bad_validation()):
        code = bc.main(_common_non_strict_argv(inp, out, summ))
    assert code == 0


# 9. Non-strict validation failure → JSONL output written
def test_non_strict_validation_failure_writes_output(tmp_path):
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    with mock.patch("scripts.build_chunks.validate_chunks", return_value=_bad_validation()):
        bc.main(_common_non_strict_argv(inp, out, summ))
    assert out.is_file()
    assert out.stat().st_size > 0


# 10. Non-strict validation failure → summary written
def test_non_strict_validation_failure_writes_summary(tmp_path):
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    with mock.patch("scripts.build_chunks.validate_chunks", return_value=_bad_validation()):
        bc.main(_common_non_strict_argv(inp, out, summ))
    assert summ.is_file()
    assert summ.stat().st_size > 0


# 11. Non-strict → summary["validation"]["is_valid"] is False
def test_non_strict_summary_marks_validation_false(tmp_path):
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    with mock.patch("scripts.build_chunks.validate_chunks", return_value=_bad_validation()):
        bc.main(_common_non_strict_argv(inp, out, summ))
    obj = json.loads(summ.read_text(encoding="utf-8"))
    assert obj["validation"]["is_valid"] is False


# 12. Non-strict → warning logged
def test_non_strict_logs_warning(tmp_path, caplog):
    import logging
    from scripts import build_chunks as bc
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    with caplog.at_level(logging.WARNING, logger="scripts.build_chunks"):
        with mock.patch("scripts.build_chunks.validate_chunks", return_value=_bad_validation()):
            bc.main(_common_non_strict_argv(inp, out, summ))
    joined = "\n".join(caplog.messages).lower()
    assert "validation" in joined and ("failed" in joined or "error" in joined or "warning" in joined)


# ---------------------------------------------------------------------------
# Determinism tests (Prompt 4C2C)
# ---------------------------------------------------------------------------

def _run_twice(tmp_path):
    """Run CLI twice with same input, different output paths. Return (out1, summ1, out2, summ2)."""
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out1 = tmp_path / "run1" / "out.jsonl"
    summ1 = tmp_path / "run1" / "summ.json"
    out2 = tmp_path / "run2" / "out.jsonl"
    summ2 = tmp_path / "run2" / "summ.json"
    base_argv = [
        "--tokenizer", "regex-estimate-v1",
        "--log-level", "WARNING",
    ]
    _run_cli(["--input", str(inp), "--output", str(out1), "--summary-output", str(summ1)] + base_argv)
    _run_cli(["--input", str(inp), "--output", str(out2), "--summary-output", str(summ2)] + base_argv)
    return out1, summ1, out2, summ2


# 1. JSONL byte-for-byte across two runs
def test_jsonl_deterministic_bytes(tmp_path):
    out1, _, out2, _ = _run_twice(tmp_path)
    assert out1.read_bytes() == out2.read_bytes(), "JSONL bytes differ between runs"


# 2. Summary byte-for-byte across two runs
def test_summary_deterministic_bytes(tmp_path):
    _, summ1, _, summ2 = _run_twice(tmp_path)
    assert summ1.read_bytes() == summ2.read_bytes(), "Summary bytes differ between runs"


# 3. JSONL has trailing newline
def test_jsonl_has_trailing_newline(tmp_path):
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out), "--summary-output", str(summ),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])
    raw = out.read_bytes()
    assert raw.endswith(b"\n"), "JSONL does not end with newline"


# 4. Summary has trailing newline
def test_summary_has_trailing_newline(tmp_path):
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out), "--summary-output", str(summ),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])
    raw = summ.read_bytes()
    assert raw.endswith(b"\n"), "Summary does not end with newline"


# 5. Chunk order is stable across two runs
def test_jsonl_line_order_is_stable(tmp_path):
    out1, _, out2, _ = _run_twice(tmp_path)
    lines1 = out1.read_text(encoding="utf-8").splitlines()
    lines2 = out2.read_text(encoding="utf-8").splitlines()
    assert lines1 == lines2, "Chunk order differs between runs"


# 6. JSONL non-empty line count matches summary chunk_count
def test_jsonl_line_count_matches_chunk_count(tmp_path):
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out), "--summary-output", str(summ),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])
    non_empty = [l for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    summary_obj = json.loads(summ.read_text(encoding="utf-8"))
    assert len(non_empty) == summary_obj["chunk_count"]


# 7. Every JSONL line is valid JSON dict
def test_each_jsonl_line_is_valid_json(tmp_path):
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out), "--summary-output", str(summ),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])
    for i, line in enumerate(out.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        obj = json.loads(line)
        assert isinstance(obj, dict), f"Line {i} is not a JSON object"


# 8. Summary has no volatile timestamp/build fields at top level
def test_summary_has_no_volatile_fields(tmp_path):
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    out = tmp_path / "out.jsonl"
    summ = tmp_path / "summ.json"
    _run_cli(["--input", str(inp), "--output", str(out), "--summary-output", str(summ),
              "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])
    obj = json.loads(summ.read_text(encoding="utf-8"))
    volatile_keys = {"timestamp", "generated_at", "created_at", "build_time"}
    present = volatile_keys & obj.keys()
    assert not present, f"Volatile fields found in summary: {present}"


# 9. Running CLI twice does not mutate the in-memory corpus (file unchanged)
def test_same_input_does_not_mutate_corpus(tmp_path):
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)
    original_bytes = inp.read_bytes()

    out1 = tmp_path / "out1.jsonl"
    summ1 = tmp_path / "summ1.json"
    out2 = tmp_path / "out2.jsonl"
    summ2 = tmp_path / "summ2.json"

    for out, summ in [(out1, summ1), (out2, summ2)]:
        _run_cli(["--input", str(inp), "--output", str(out), "--summary-output", str(summ),
                  "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])

    assert inp.read_bytes() == original_bytes, "Input file was mutated"


# 10. Different output paths do not change JSONL/summary content
def test_different_output_paths_do_not_change_content(tmp_path):
    """Content must be identical even when output filenames differ."""
    inp = tmp_path / "corpus.json"
    _write_corpus(inp)

    out_a = tmp_path / "alpha" / "chunks.jsonl"
    summ_a = tmp_path / "alpha" / "summary.json"
    out_b = tmp_path / "beta" / "legal_chunks.jsonl"
    summ_b = tmp_path / "beta" / "chunking_summary.json"

    for out, summ in [(out_a, summ_a), (out_b, summ_b)]:
        _run_cli(["--input", str(inp), "--output", str(out), "--summary-output", str(summ),
                  "--tokenizer", "regex-estimate-v1", "--log-level", "WARNING"])

    assert out_a.read_bytes() == out_b.read_bytes(), "JSONL content differs with different output paths"
    assert summ_a.read_bytes() == summ_b.read_bytes(), "Summary content differs with different output paths"
