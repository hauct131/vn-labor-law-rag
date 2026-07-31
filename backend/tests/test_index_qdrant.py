"""Unit tests for production-safe Qdrant Indexer (backend/app/ingestion/index_qdrant.py).

Uses mocks and fakes for FastEmbed models and QdrantClient to test preflight checks,
schema validation, resume rules, payload integrity, error conditions, and dry-run mode.
"""

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.app.ingestion.index_qdrant import (
    DEFAULT_AUDIT_SUMMARY,
    DEFAULT_CHUNKS,
    DEFAULT_EXPECTED_CHUNKS,
    DEFAULT_EXPECTED_SHA256,
    IndexerError,
    build_chunk_payload,
    build_parser,
    compute_sha256,
    e5_document_text,
    is_valid_uuid,
    load_and_validate_chunks,
    run_indexer,
    validate_audit_summary,
)


@pytest.fixture
def sample_chunk_id() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def sample_chunks(sample_chunk_id: str) -> list[dict]:
    return [
        {
            "chunk_id": sample_chunk_id,
            "chunk_key": "art_1|clause=1",
            "chunk_type": "clause",
            "content": "Nội dung quy định về hợp đồng lao động.",
            "article_code": "LQ.1",
            "article_title": "Quy định chung",
            "document_id": "doc_1",
            "source_document_id": "src_1",
        }
    ]


@pytest.fixture
def corpus_file(tmp_path: Path, sample_chunks: list[dict]) -> Path:
    file_path = tmp_path / "legal_chunks.jsonl"
    lines = [json.dumps(c, ensure_ascii=False) for c in sample_chunks]
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return file_path


@pytest.fixture
def audit_summary_file(tmp_path: Path, corpus_file: Path, sample_chunks: list[dict]) -> Path:
    sha = compute_sha256(corpus_file)
    summary = {
        "status": "completed",
        "input_sha256": sha,
        "chunk_count": len(sample_chunks),
        "model_name": "intfloat/multilingual-e5-large",
        "exact_measurement": True,
        "risk_counts": {
            "near_or_above_count": 0,
            "strictly_over_model_limit_count": 0,
        },
    }
    file_path = tmp_path / "audit_summary.json"
    file_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return file_path

@pytest.fixture(autouse=True)
def isolate_qdrant_summary_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent tests from writing index summaries into production data paths."""
    monkeypatch.setattr(
        "backend.app.ingestion.index_qdrant.DEFAULT_SUMMARY_OUTPUT",
        tmp_path / "qdrant_index" / "summary.json",
    )


# ---------------------------------------------------------------------------
# Test 1: Valid corpus loaded correctly
# ---------------------------------------------------------------------------
def test_valid_corpus_loaded_correctly(corpus_file: Path, sample_chunks: list[dict]):
    chunks, sha = load_and_validate_chunks(corpus_file, expected_chunks=1)
    assert len(chunks) == 1
    assert chunks[0]["chunk_id"] == sample_chunks[0]["chunk_id"]
    assert sha == compute_sha256(corpus_file)


# ---------------------------------------------------------------------------
# Test 2: Reject invalid JSON line, missing chunk_id/content, empty content
# ---------------------------------------------------------------------------
def test_reject_invalid_json_line(tmp_path: Path):
    file_path = tmp_path / "invalid.jsonl"
    file_path.write_text("not json content\n", encoding="utf-8")
    with pytest.raises(IndexerError, match="Invalid JSON"):
        load_and_validate_chunks(file_path)


def test_reject_missing_chunk_id(tmp_path: Path):
    file_path = tmp_path / "missing_id.jsonl"
    file_path.write_text(json.dumps({"content": "text"}) + "\n", encoding="utf-8")
    with pytest.raises(IndexerError, match="invalid or missing UUID chunk_id"):
        load_and_validate_chunks(file_path)


def test_reject_empty_content(tmp_path: Path):
    file_path = tmp_path / "empty_content.jsonl"
    chunk_id = str(uuid.uuid4())
    file_path.write_text(json.dumps({"chunk_id": chunk_id, "content": "   "}) + "\n", encoding="utf-8")
    with pytest.raises(IndexerError, match="has empty content"):
        load_and_validate_chunks(file_path)


# ---------------------------------------------------------------------------
# Test 3: Reject duplicate or invalid UUID chunk_id
# ---------------------------------------------------------------------------
def test_reject_invalid_uuid(tmp_path: Path):
    file_path = tmp_path / "invalid_uuid.jsonl"
    file_path.write_text(json.dumps({"chunk_id": "not-a-uuid", "content": "text"}) + "\n", encoding="utf-8")
    with pytest.raises(IndexerError, match="invalid or missing UUID chunk_id"):
        load_and_validate_chunks(file_path)


def test_reject_duplicate_uuid(tmp_path: Path):
    file_path = tmp_path / "dup.jsonl"
    cid = str(uuid.uuid4())
    l1 = json.dumps({"chunk_id": cid, "content": "c1"})
    l2 = json.dumps({"chunk_id": cid, "content": "c2"})
    file_path.write_text(f"{l1}\n{l2}\n", encoding="utf-8")
    with pytest.raises(IndexerError, match="Duplicate chunk_id"):
        load_and_validate_chunks(file_path)


# ---------------------------------------------------------------------------
# Test 4: Reject expected count mismatch
# ---------------------------------------------------------------------------
def test_reject_expected_count_mismatch(corpus_file: Path):
    with pytest.raises(IndexerError, match="Chunk count mismatch"):
        load_and_validate_chunks(corpus_file, expected_chunks=999)


# ---------------------------------------------------------------------------
# Test 5: Reject SHA mismatch
# ---------------------------------------------------------------------------
def test_reject_sha_mismatch(corpus_file: Path):
    wrong_sha = "0000000000000000000000000000000000000000000000000000000000000000"
    with pytest.raises(IndexerError, match="Corpus SHA-256 mismatch"):
        load_and_validate_chunks(corpus_file, expected_sha256=wrong_sha)


# ---------------------------------------------------------------------------
# Test 6: Reject missing/invalid/mismatched audit summary file or model
# ---------------------------------------------------------------------------
def test_reject_missing_audit_summary(tmp_path: Path):
    missing_path = tmp_path / "nonexistent.json"
    with pytest.raises(IndexerError, match="audit summary file not found"):
        validate_audit_summary(missing_path, "sha", "model", 1)


def test_reject_audit_model_mismatch(audit_summary_file: Path, corpus_file: Path):
    sha = compute_sha256(corpus_file)
    with pytest.raises(IndexerError, match="model_name mismatch"):
        validate_audit_summary(audit_summary_file, sha, "wrong-model-name", 1)


# ---------------------------------------------------------------------------
# Test 7: Reject over-limit or near-limit chunks in audit summary
# ---------------------------------------------------------------------------
def test_reject_audit_over_limit(tmp_path: Path, corpus_file: Path):
    sha = compute_sha256(corpus_file)
    summary = {
        "input_sha256": sha,
        "chunk_count": 1,
        "model_name": "intfloat/multilingual-e5-large",
        "exact_measurement": True,
        "risk_counts": {"near_or_above_count": 0, "strictly_over_model_limit_count": 2},
    }
    path = tmp_path / "summary_over.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(IndexerError, match="strictly exceed model limit"):
        validate_audit_summary(path, sha, "intfloat/multilingual-e5-large", 1)


def test_reject_audit_near_limit(tmp_path: Path, corpus_file: Path):
    sha = compute_sha256(corpus_file)
    summary = {
        "input_sha256": sha,
        "chunk_count": 1,
        "model_name": "intfloat/multilingual-e5-large",
        "exact_measurement": True,
        "risk_counts": {"near_or_above_count": 1, "strictly_over_model_limit_count": 0},
    }
    path = tmp_path / "summary_near.json"
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(IndexerError, match="near model limit"):
        validate_audit_summary(path, sha, "intfloat/multilingual-e5-large", 1)


# ---------------------------------------------------------------------------
# Test 8: E5 passage prefix applied correctly and no double-prefixing
# ---------------------------------------------------------------------------
def test_e5_passage_prefix_logic():
    raw_text = "Nội dung điều luật"
    formatted = e5_document_text(raw_text, "intfloat/multilingual-e5-large")
    assert formatted == "passage: Nội dung điều luật"

    # No double prefix
    already_prefixed = "passage: Nội dung đã có prefix"
    formatted_again = e5_document_text(already_prefixed, "intfloat/multilingual-e5-large")
    assert formatted_again == "passage: Nội dung đã có prefix"


# ---------------------------------------------------------------------------
# Test 9: --dry-run does not create client, load model, or write Qdrant
# ---------------------------------------------------------------------------
def test_dry_run_no_client_or_models(corpus_file: Path, audit_summary_file: Path):
    sha = compute_sha256(corpus_file)
    parser = build_parser()
    args = parser.parse_args([
        "--chunks", str(corpus_file),
        "--audit-summary", str(audit_summary_file),
        "--expected-chunks", "1",
        "--expected-sha256", sha,
        "--dry-run",
    ])
    with patch("qdrant_client.QdrantClient") as mock_qdrant:
        res = run_indexer(args)
        assert res["status"] == "dry_run_success"
        mock_qdrant.assert_not_called()


# ---------------------------------------------------------------------------
# Test 10: Existing collection without --resume fails
# ---------------------------------------------------------------------------
def test_existing_collection_without_resume_fails(corpus_file: Path, audit_summary_file: Path):
    sha = compute_sha256(corpus_file)
    parser = build_parser()
    args = parser.parse_args([
        "--chunks", str(corpus_file),
        "--audit-summary", str(audit_summary_file),
        "--expected-chunks", "1",
        "--expected-sha256", sha,
        "--collection", "labor_law_test",
    ])

    mock_client = MagicMock()
    mock_client.collection_exists.return_value = True

    with patch("qdrant_client.QdrantClient", return_value=mock_client), \
         patch("fastembed.TextEmbedding"), \
         patch("fastembed.SparseTextEmbedding"):
        with pytest.raises(IndexerError, match="already exists"):
            run_indexer(args)


# ---------------------------------------------------------------------------
# Test 11: Code NEVER calls delete_collection
# ---------------------------------------------------------------------------
def test_code_never_calls_delete_collection():
    import backend.app.ingestion.index_qdrant as module
    source_code = Path(module.__file__).read_text(encoding="utf-8")
    assert "delete_collection" not in source_code


# ---------------------------------------------------------------------------
# Test 12: Create collection uses correct dense/sparse schema
# ---------------------------------------------------------------------------
def test_create_collection_schema(corpus_file: Path, audit_summary_file: Path, sample_chunks: list[dict]):
    sha = compute_sha256(corpus_file)
    parser = build_parser()
    args = parser.parse_args([
        "--chunks", str(corpus_file),
        "--audit-summary", str(audit_summary_file),
        "--expected-chunks", "1",
        "--expected-sha256", sha,
        "--collection", "labor_law_test",
    ])

    mock_client = MagicMock()
    mock_client.collection_exists.side_effect = [False, False]
    mock_client.count.return_value.count = 1

    # Return retrieve for post-index verification
    mock_point = MagicMock()
    mock_point.payload = build_chunk_payload(sample_chunks[0], sha, 1, "dense", "sparse")
    mock_client.retrieve.return_value = [mock_point]

    mock_dense = MagicMock()
    mock_dense.embed.return_value = [[0.1] * 1024]

    mock_sparse_emb = MagicMock()
    mock_sparse_emb.indices = [0, 5]
    mock_sparse_emb.values = [0.5, 0.9]
    mock_sparse = MagicMock()
    mock_sparse.embed.return_value = [mock_sparse_emb]

    with patch("qdrant_client.QdrantClient", return_value=mock_client), \
         patch("fastembed.TextEmbedding", return_value=mock_dense), \
         patch("fastembed.SparseTextEmbedding", return_value=mock_sparse):
        run_indexer(args)
        mock_client.create_collection.assert_called_once()
        kwargs = mock_client.create_collection.call_args.kwargs
        assert kwargs["collection_name"] == "labor_law_test"
        assert "dense" in kwargs["vectors_config"]
        assert kwargs["vectors_config"]["dense"].size == 1024
        assert "sparse" in kwargs["sparse_vectors_config"]


# ---------------------------------------------------------------------------
# Test 13: Dense vector wrong dimension (not 1024) fails before upsert
# ---------------------------------------------------------------------------
def test_dense_vector_wrong_dimension_fails(corpus_file: Path, audit_summary_file: Path):
    sha = compute_sha256(corpus_file)
    parser = build_parser()
    args = parser.parse_args([
        "--chunks", str(corpus_file),
        "--audit-summary", str(audit_summary_file),
        "--expected-chunks", "1",
        "--expected-sha256", sha,
        "--collection", "labor_law_test",
    ])

    mock_client = MagicMock()
    mock_client.collection_exists.return_value = False

    # Return wrong size vector (512 instead of 1024)
    mock_dense = MagicMock()
    mock_dense.embed.return_value = [[0.1] * 512]

    mock_sparse_emb = MagicMock()
    mock_sparse_emb.indices = [0]
    mock_sparse_emb.values = [0.5]
    mock_sparse = MagicMock()
    mock_sparse.embed.return_value = [mock_sparse_emb]

    with patch("qdrant_client.QdrantClient", return_value=mock_client), \
         patch("fastembed.TextEmbedding", return_value=mock_dense), \
         patch("fastembed.SparseTextEmbedding", return_value=mock_sparse):
        with pytest.raises(IndexerError, match="Dense vector size mismatch"):
            run_indexer(args)
        mock_client.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Test 14: Batching and stable UUID point IDs
# ---------------------------------------------------------------------------
def test_batching_and_stable_ids(corpus_file: Path, audit_summary_file: Path, sample_chunk_id: str, sample_chunks: list[dict]):
    sha = compute_sha256(corpus_file)
    parser = build_parser()
    args = parser.parse_args([
        "--chunks", str(corpus_file),
        "--audit-summary", str(audit_summary_file),
        "--expected-chunks", "1",
        "--expected-sha256", sha,
        "--collection", "labor_law_test",
    ])

    mock_client = MagicMock()
    mock_client.collection_exists.side_effect = [False, False]
    mock_client.count.return_value.count = 1

    mock_point = MagicMock()
    mock_point.payload = build_chunk_payload(sample_chunks[0], sha, 1, "dense", "sparse")
    mock_client.retrieve.return_value = [mock_point]

    mock_dense = MagicMock()
    mock_dense.embed.return_value = [[0.1] * 1024]
    mock_sparse_emb = MagicMock()
    mock_sparse_emb.indices = [0]
    mock_sparse_emb.values = [0.5]
    mock_sparse = MagicMock()
    mock_sparse.embed.return_value = [mock_sparse_emb]

    with patch("qdrant_client.QdrantClient", return_value=mock_client), \
         patch("fastembed.TextEmbedding", return_value=mock_dense), \
         patch("fastembed.SparseTextEmbedding", return_value=mock_sparse):
        run_indexer(args)
        assert mock_client.upsert.called
        points = mock_client.upsert.call_args.kwargs["points"]
        assert len(points) == 1
        assert points[0].id == sample_chunk_id


# ---------------------------------------------------------------------------
# Test 15: Payload contains content, provenance, and _index_corpus_sha256
# ---------------------------------------------------------------------------
def test_payload_structure(sample_chunks: list[dict]):
    chunk = sample_chunks[0]
    payload = build_chunk_payload(chunk, "sha123", 1, "dense-model", "sparse-model")
    assert payload["content"] == chunk["content"]
    assert payload["article_code"] == chunk["article_code"]
    assert payload["_index_corpus_sha256"] == "sha123"
    assert payload["_index_corpus_chunk_count"] == 1
    assert payload["_index_dense_model"] == "dense-model"
    assert payload["_index_sparse_model"] == "sparse-model"
    assert payload["_indexer_version"] == "1.1.0"


def test_defaults_target_unified_833_release():
    expected_chunks = (
        Path(__file__).resolve().parents[2]
        / "data/releases/labor-law-2026-07-28-candidate/chunks.jsonl"
    )

    assert DEFAULT_CHUNKS.resolve() == expected_chunks.resolve()
    assert str(DEFAULT_AUDIT_SUMMARY) == (
        "data/quality/unified_e5_token_audit/summary.json"
    )
    assert DEFAULT_EXPECTED_CHUNKS == 833
    assert DEFAULT_EXPECTED_SHA256 == (
        "fd35bb1a94a3036f7977781de17bb1b49"
        "b12c58be61fc74efac68dcf8a7a8c54"
    )
# ---------------------------------------------------------------------------
# Test 16: --resume on collection with matching fingerprint succeeds
# ---------------------------------------------------------------------------
def test_resume_matching_fingerprint_succeeds(corpus_file: Path, audit_summary_file: Path, sample_chunks: list[dict]):
    sha = compute_sha256(corpus_file)
    parser = build_parser()
    args = parser.parse_args([
        "--chunks", str(corpus_file),
        "--audit-summary", str(audit_summary_file),
        "--expected-chunks", "1",
        "--expected-sha256", sha,
        "--collection", "labor_law_test",
        "--resume",
    ])

    mock_client = MagicMock()
    mock_client.collection_exists.return_value = True

    # Collection params check
    mock_dense_param = MagicMock()
    mock_dense_param.size = 1024
    mock_coll_info = MagicMock()
    mock_coll_info.config.params.vectors = {"dense": mock_dense_param}
    mock_coll_info.config.params.sparse_vectors = {"sparse": MagicMock()}
    mock_client.get_collection.return_value = mock_coll_info

    # Scroll check for resume
    mock_existing_pt = MagicMock()
    mock_existing_pt.id = sample_chunks[0]["chunk_id"]
    mock_existing_pt.payload = {"_index_corpus_sha256": sha}
    mock_client.scroll.return_value = ([mock_existing_pt], None)
    mock_client.count.return_value.count = 1

    mock_point = MagicMock()
    mock_point.payload = build_chunk_payload(sample_chunks[0], sha, 1, "dense", "sparse")
    mock_client.retrieve.return_value = [mock_point]

    mock_dense = MagicMock()
    mock_dense.embed.return_value = [[0.1] * 1024]
    mock_sparse_emb = MagicMock()
    mock_sparse_emb.indices = [0]
    mock_sparse_emb.values = [0.5]
    mock_sparse = MagicMock()
    mock_sparse.embed.return_value = [mock_sparse_emb]

    with patch("qdrant_client.QdrantClient", return_value=mock_client), \
         patch("fastembed.TextEmbedding", return_value=mock_dense), \
         patch("fastembed.SparseTextEmbedding", return_value=mock_sparse):
        res = run_indexer(args)
        assert res["status"] == "completed"


# ---------------------------------------------------------------------------
# Test 17: --resume on collection with mismatched/missing fingerprint rejected
# ---------------------------------------------------------------------------
def test_resume_mismatched_fingerprint_rejected(corpus_file: Path, audit_summary_file: Path, sample_chunks: list[dict]):
    sha = compute_sha256(corpus_file)
    parser = build_parser()
    args = parser.parse_args([
        "--chunks", str(corpus_file),
        "--audit-summary", str(audit_summary_file),
        "--expected-chunks", "1",
        "--expected-sha256", sha,
        "--collection", "labor_law_test",
        "--resume",
    ])

    mock_client = MagicMock()
    mock_client.collection_exists.return_value = True

    mock_dense_param = MagicMock()
    mock_dense_param.size = 1024
    mock_coll_info = MagicMock()
    mock_coll_info.config.params.vectors = {"dense": mock_dense_param}
    mock_coll_info.config.params.sparse_vectors = {"sparse": MagicMock()}
    mock_client.get_collection.return_value = mock_coll_info

    # Mismatched fingerprint
    mock_existing_pt = MagicMock()
    mock_existing_pt.id = sample_chunks[0]["chunk_id"]
    mock_existing_pt.payload = {"_index_corpus_sha256": "wrong_sha"}
    mock_client.scroll.return_value = ([mock_existing_pt], None)
    mock_client.count.return_value.count = 1

    with patch("qdrant_client.QdrantClient", return_value=mock_client), \
         patch("fastembed.TextEmbedding"), \
         patch("fastembed.SparseTextEmbedding"):
        with pytest.raises(IndexerError, match="mismatched or missing corpus SHA-256 fingerprint"):
            run_indexer(args)


# ---------------------------------------------------------------------------
# Test 18: Post-index count != expected_chunks fails
# ---------------------------------------------------------------------------
def test_post_index_count_mismatch_fails(corpus_file: Path, audit_summary_file: Path):
    sha = compute_sha256(corpus_file)
    parser = build_parser()
    args = parser.parse_args([
        "--chunks", str(corpus_file),
        "--audit-summary", str(audit_summary_file),
        "--expected-chunks", "1",
        "--expected-sha256", sha,
        "--collection", "labor_law_test",
    ])

    mock_client = MagicMock()
    mock_client.collection_exists.side_effect = [False, False]
    # Return count 0 post indexing
    mock_client.count.return_value.count = 0

    mock_dense = MagicMock()
    mock_dense.embed.return_value = [[0.1] * 1024]
    mock_sparse_emb = MagicMock()
    mock_sparse_emb.indices = [0]
    mock_sparse_emb.values = [0.5]
    mock_sparse = MagicMock()
    mock_sparse.embed.return_value = [mock_sparse_emb]

    with patch("qdrant_client.QdrantClient", return_value=mock_client), \
         patch("fastembed.TextEmbedding", return_value=mock_dense), \
         patch("fastembed.SparseTextEmbedding", return_value=mock_sparse):
        with pytest.raises(IndexerError, match="Post-indexing count mismatch"):
            run_indexer(args)


# ---------------------------------------------------------------------------
# Test 19: --verify-only does not write vectors
# ---------------------------------------------------------------------------
def test_verify_only_does_not_write_vectors(corpus_file: Path, audit_summary_file: Path, sample_chunks: list[dict]):
    sha = compute_sha256(corpus_file)
    parser = build_parser()
    args = parser.parse_args([
        "--chunks", str(corpus_file),
        "--audit-summary", str(audit_summary_file),
        "--expected-chunks", "1",
        "--expected-sha256", sha,
        "--collection", "labor_law_test",
        "--verify-only",
    ])

    mock_client = MagicMock()
    mock_client.collection_exists.return_value = True

    mock_dense_param = MagicMock()
    mock_dense_param.size = 1024
    mock_coll_info = MagicMock()
    mock_coll_info.config.params.vectors = {"dense": mock_dense_param}
    mock_coll_info.config.params.sparse_vectors = {"sparse": MagicMock()}
    mock_client.get_collection.return_value = mock_coll_info

    mock_client.count.return_value.count = 1
    mock_point = MagicMock()
    mock_point.payload = build_chunk_payload(sample_chunks[0], sha, 1, "dense", "sparse")
    mock_client.retrieve.return_value = [mock_point]

    with patch("qdrant_client.QdrantClient", return_value=mock_client), \
         patch("fastembed.TextEmbedding") as mock_dense_cls, \
         patch("fastembed.SparseTextEmbedding") as mock_sparse_cls:
        res = run_indexer(args)
        assert res["status"] == "verified"
        assert args.summary_output.is_file()
        mock_dense_cls.assert_not_called()
        mock_sparse_cls.assert_not_called()
        mock_client.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Test 20: CLI --help exits with code 0
# ---------------------------------------------------------------------------
def test_cli_help_exits_zero():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0


def test_indexer_passes_qdrant_api_key_without_reporting_secret(
    corpus_file: Path,
    audit_summary_file: Path,
    sample_chunks: list[dict],
):
    sha = compute_sha256(corpus_file)
    args = build_parser().parse_args([
        "--chunks", str(corpus_file),
        "--audit-summary", str(audit_summary_file),
        "--expected-chunks", "1",
        "--expected-sha256", sha,
        "--collection", "labor_law_test",
        "--qdrant-api-key", "test-secret",
        "--verify-only",
    ])

    mock_client = MagicMock()
    mock_dense_param = MagicMock(size=1024)
    mock_collection = MagicMock()
    mock_collection.config.params.vectors = {"dense": mock_dense_param}
    mock_collection.config.params.sparse_vectors = {"sparse": MagicMock()}
    mock_client.get_collection.return_value = mock_collection
    mock_client.count.return_value.count = 1
    mock_point = MagicMock()
    mock_point.payload = build_chunk_payload(
        sample_chunks[0], sha, 1, "dense", "sparse"
    )
    mock_client.retrieve.return_value = [mock_point]

    with patch(
        "qdrant_client.QdrantClient",
        return_value=mock_client,
    ) as client_class:
        report = run_indexer(args)

    client_class.assert_called_once_with(
        url=args.qdrant_url,
        api_key="test-secret",
    )
    assert report["status"] == "verified"
    assert "test-secret" not in json.dumps(report)
