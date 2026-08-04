from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_runtime_golden_splits import (
    SplitError,
    build_outputs,
    build_parser,
    run,
)


SOURCE = Path("data/evaluation/golden_questions_v4_canonical_word_candidate.json")


def test_locked_split_has_expected_counts_balance_and_no_article_leakage():
    dev, test, manifest = build_outputs(SOURCE)

    assert dev["locked"] is True
    assert test["locked"] is True
    assert dev["lock_scope"] == "retrieval_labels_and_split_only"
    assert test["authority_review_status"] == "pending"
    assert manifest["counts"] == {
        "source": 45,
        "dev": 31,
        "test": 13,
        "disabled": 1,
    }
    assert manifest["category_counts"]["test"] == {
        "exact_legal_term": 3,
        "multi_article": 2,
        "natural_language": 3,
        "single_scenario": 5,
    }
    assert manifest["article_codes"]["cross_split_overlap"] == []
    assert {q["benchmark_split"] for q in dev["questions"]} == {"dev"}
    assert {q["benchmark_split"] for q in test["questions"]} == {"test"}

    expected_binding = {
        "release_id": dev["corpus"]["release_id"],
        "canonical_chunks_sha256": dev["corpus"]["sha256"],
        "chunk_count": dev["corpus"]["chunk_count"],
    }
    repo_root = Path(__file__).resolve().parents[2]

    for dataset in (dev, test):
        assert dataset["labeling"]["release_binding"] == expected_binding
        assert "source_labeling_method" in dataset["labeling"]
        assert dataset["corpus"]["release_status"] == (
            "academic_final_dataset_pending_authority_review"
        )

        for field in ("path", "article_path"):
            corpus_path = Path(dataset["corpus"][field])
            assert not corpus_path.is_absolute()
            assert (repo_root / corpus_path).is_file()


def test_split_check_detects_stale_output(tmp_path: Path):
    args = build_parser().parse_args([
        "--source", str(SOURCE),
        "--output-dir", str(tmp_path),
    ])
    assert run(args)["status"] == "written"

    check_args = build_parser().parse_args([
        "--source", str(SOURCE),
        "--output-dir", str(tmp_path),
        "--check",
    ])
    assert run(check_args)["status"] == "check_pass"

    path = tmp_path / "golden_v3_test.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["questions"].pop()
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(SplitError, match="stale"):
        run(check_args)
