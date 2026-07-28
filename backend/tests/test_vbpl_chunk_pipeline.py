"""
Integration test suite for the VBPL Chunking Pipeline.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.app.ingestion.legal_chunker import (
    ChunkingConfig,
    TiktokenTokenCounter,
    build_legal_chunks,
    validate_chunks,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_CORPUS_PATH = REPO_ROOT / "data" / "processed" / "vbpl_articles_raw.json"
CHUNKS_PATH = REPO_ROOT / "data" / "processed" / "vbpl_legal_chunks.jsonl"


@pytest.fixture(scope="module")
def vbpl_corpus():
    if not RAW_CORPUS_PATH.exists():
        pytest.skip(f"Raw corpus file missing at {RAW_CORPUS_PATH}")
    return json.loads(RAW_CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def vbpl_chunks(vbpl_corpus):
    tc = TiktokenTokenCounter()
    cfg = ChunkingConfig(target_tokens=400, max_tokens=600)
    return build_legal_chunks(vbpl_corpus, config=cfg, token_counter=tc)


def test_vbpl_chunk_key_uniqueness(vbpl_corpus, vbpl_chunks):
    chunk_ids = [c["chunk_id"] for c in vbpl_chunks]
    chunk_keys = [c["chunk_key"] for c in vbpl_chunks]

    assert len(chunk_ids) == len(set(chunk_ids)), "Duplicate chunk_id found in VBPL chunks"
    assert len(chunk_keys) == len(set(chunk_keys)), "Duplicate chunk_key found in VBPL chunks"


def test_vbpl_chunk_provenance_fields(vbpl_chunks):
    required_fields = [
        "chunk_id",
        "article_id",
        "document_id",
        "document_number",
        "source_document_id",
        "source_item_id",
        "source_adapter",
        "corpus_role",
        "source_urls",
        "source_sha256",
    ]

    for chunk in vbpl_chunks:
        for field in required_fields:
            val = chunk.get(field)
            assert val is not None, f"Chunk {chunk.get('chunk_id')} missing field {field}"
            if isinstance(val, (str, list)):
                assert len(val) > 0, f"Chunk {chunk.get('chunk_id')} field {field} is empty"


def test_vbpl_article_coverage(vbpl_corpus, vbpl_chunks):
    expected_articles = {a["article_id"] for a in vbpl_corpus["articles"]}
    covered_articles = {c.get("article_id") for c in vbpl_chunks}

    assert len(expected_articles) == 285, f"Expected 285 articles, got {len(expected_articles)}"
    missing = expected_articles - covered_articles
    assert len(missing) == 0, f"Missing coverage for articles: {missing}"


def test_vbpl_cli_strict_build(tmp_path):
    output_chunks = tmp_path / "test_chunks.jsonl"
    output_summary = tmp_path / "test_summary.json"

    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "build_chunks.py"),
        "--input",
        str(RAW_CORPUS_PATH),
        "--output",
        str(output_chunks),
        "--summary-output",
        str(output_summary),
        "--target-tokens",
        "400",
        "--max-tokens",
        "600",
        "--strict",
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert res.returncode == 0, f"build_chunks CLI failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert output_chunks.exists()
    assert output_summary.exists()

    summary_data = json.loads(output_summary.read_text(encoding="utf-8"))
    assert summary_data["validation"]["is_valid"] is True
    assert summary_data["validation"]["error_count"] == 0


def test_vbpl_e5_token_overflow():
    """Verify that the exact pre-truncation E5 audit is fresh and valid."""
    audit_path = (
        REPO_ROOT
        / "data"
        / "quality"
        / "vbpl_e5_token_audit"
        / "summary.json"
    )

    assert CHUNKS_PATH.is_file(), f"Missing chunk artifact: {CHUNKS_PATH}"
    assert audit_path.is_file(), f"Missing E5 audit artifact: {audit_path}"

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    chunks_sha256 = hashlib.sha256(CHUNKS_PATH.read_bytes()).hexdigest()
    chunk_count = sum(
        1
        for line in CHUNKS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )

    assert audit["status"] == "completed"
    assert audit["input_sha256"] == chunks_sha256, (
        "E5 audit is stale: its input hash differs from the current chunks"
    )
    assert audit["chunk_count"] == chunk_count
    assert audit["model_name"] == "intfloat/multilingual-e5-large"
    assert audit["fastembed_version"] == "0.8.0"
    assert audit["exact_measurement"] is True
    assert audit["actual_model_max_tokens"] == 512
    assert audit["validation"]["is_valid"] is True

    max_tokens = audit["token_statistics"]["max"]
    overflow_count = audit["risk_counts"][
        "strictly_over_model_limit_count"
    ]

    assert max_tokens <= audit["actual_model_max_tokens"]
    assert overflow_count == 0, (
        f"Found {overflow_count} chunks exceeding the E5 limit"
    )


def test_existing_legal_chunker_regression(vbpl_corpus):
    tc = TiktokenTokenCounter()
    cfg = ChunkingConfig()
    chunks = build_legal_chunks(vbpl_corpus, config=cfg, token_counter=tc)
    validation = validate_chunks(vbpl_corpus, chunks, config=cfg, token_counter=tc)

    assert validation["is_valid"] is True, f"Validation failed: {validation.get('errors')[:5]}"
