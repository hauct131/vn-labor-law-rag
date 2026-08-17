"""Regression tests for active canonical Word 804 entry points."""

from __future__ import annotations

from pathlib import Path

from scripts.benchmark_runtime_retrieval import build_parser as benchmark_parser
from scripts.build_runtime_golden_splits import build_parser as split_parser
from scripts.evaluate_retrieval_core import build_parser as core_parser


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_RELEASE = (
    "data/releases/labor-law-canonical-word-20260804-164432-candidate"
)
CANONICAL_CHUNKS = f"{CANONICAL_RELEASE}/canonical_chunks.jsonl"


def test_active_parser_defaults_use_canonical_word_804() -> None:
    split_args = split_parser().parse_args([])
    benchmark_args = benchmark_parser().parse_args([])
    core_args = core_parser().parse_args([])

    assert str(split_args.output_dir) == (
        "data/evaluation/splits/canonical_word_804"
    )
    assert str(benchmark_args.golden) == (
        "data/evaluation/splits/canonical_word_804/golden_v3_dev.json"
    )
    assert str(benchmark_args.chunks) == CANONICAL_CHUNKS
    assert str(core_args.chunks) == CANONICAL_CHUNKS
    assert core_args.expected_chunks == 804
    assert core_args.expected_sha256 == (
        "fdbec539efbfb3f4aa3cb3962046321e3"
        "a402150d93516ef4256934972c70307"
    )


def test_makefile_separates_historical_builder_from_active_release() -> None:
    root_makefile = (PROJECT_ROOT / "Makefile").read_text(encoding="utf-8")
    eval_makefile = (
        PROJECT_ROOT / "Makefile.eval.inc"
    ).read_text(encoding="utf-8")

    assert "DOCX_BUILD_RELEASE_DIR ?=" in root_makefile
    assert "UNIFIED_RELEASE_DIR ?=" not in root_makefile
    assert f"UNIFIED_RELEASE_DIR ?= {CANONICAL_RELEASE}" in eval_makefile
    assert "UNIFIED_EXPECTED_CHUNKS ?= 804" in eval_makefile


def test_final_verifier_checks_locked_evidence_without_rerunning_test() -> None:
    verifier = (
        PROJECT_ROOT / "scripts/verify_final_release.sh"
    ).read_text(encoding="utf-8")

    assert "canonical_word_804/locked" in verifier
    assert "sha256sum -c SHA256SUMS.txt" in verifier
    assert "--split-role test" not in verifier
    assert "smoke_contract_review_browser.py" in verifier
    assert "sample_labor_contract.docx" not in verifier


def test_final_verifier_prepares_real_runtime_dependencies() -> None:
    verifier = (
        PROJECT_ROOT / "scripts/verify_final_release.sh"
    ).read_text(encoding="utf-8")

    assert (
        'RUNTIME_COLLECTION="labor_law_canonical_word_20260804_fdbec539"'
        in verifier
    )
    assert 'RUNTIME_ALIAS="labor_law_dev"' in verifier
    assert "--profile tools build runtime-assets" in verifier
    assert "--profile tools run --rm runtime-assets" in verifier
    assert "-m backend.app.ingestion.qdrant_alias" in verifier
    assert '--alias "$RUNTIME_ALIAS"' in verifier
    assert "settings.openrouter_api_key.strip()" in verifier
