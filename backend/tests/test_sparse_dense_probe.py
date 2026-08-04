"""Unit tests for the Day-4 Sparse/Dense model probe."""

from __future__ import annotations

import json
from argparse import Namespace
from types import SimpleNamespace

import pytest

from scripts.test_sparse_dense import (
    ProbeInputError,
    QueryMetric,
    build_parser,
    chunk_payload,
    e5_document_text,
    e5_query_text,
    load_chunks,
    load_questions,
    retrieve_one,
    summarize,
    truncation_risk_proxy,
    validate_args,
    validate_ground_truth,
)


def test_parser_uses_disposable_collection():
    args = build_parser().parse_args([])
    assert args.collection == "labor_law_model_probe"
    assert args.collection != "labor_law"
    assert str(args.chunks) == (
        "data/releases/labor-law-2026-07-28-candidate/chunks.jsonl"
    )
    assert args.expected_chunks == 833
    assert args.dense_model == "intfloat/multilingual-e5-large"
    assert args.dense_size == 1024
    assert args.dense_max_tokens == 512
    assert args.sparse_model == "Qdrant/bm25"
    assert args.sparse_disable_stemmer is True
    assert str(args.vncorenlp_model_dir) == "models/vncorenlp"


def test_production_collection_is_rejected():
    args = build_parser().parse_args(["--collection", "labor_law"])
    with pytest.raises(ProbeInputError, match="production collection"):
        validate_args(args)


def test_recreate_and_skip_index_are_mutually_exclusive():
    args = build_parser().parse_args(["--recreate", "--skip-index"])
    with pytest.raises(ProbeInputError, match="cannot be combined"):
        validate_args(args)


def test_sparse_only_reindex_is_mutually_exclusive():
    for option in ("--recreate", "--skip-index"):
        args = build_parser().parse_args(["--sparse-only-reindex", option])
        with pytest.raises(ProbeInputError, match="cannot be combined"):
            validate_args(args)


def test_e5_prefixes_are_asymmetric():
    model = "intfloat/multilingual-e5-large"
    assert e5_document_text("Nội dung", model) == "passage: Nội dung"
    assert e5_query_text("Câu hỏi", model) == "query: Câu hỏi"
    assert e5_document_text("Nội dung", "other-model") == "Nội dung"
    assert e5_query_text("Câu hỏi", "other-model") == "Câu hỏi"


def test_load_chunks_validates_uniqueness(tmp_path):
    path = tmp_path / "chunks.jsonl"
    chunk = {
        "chunk_id": "one",
        "content": "Nội dung",
        "article_code": "20.2.LQ.1",
    }
    path.write_text(
        json.dumps(chunk, ensure_ascii=False)
        + "\n"
        + json.dumps(chunk, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ProbeInputError, match="Duplicate chunk_id"):
        load_chunks(path)


def test_load_questions_and_ground_truth(tmp_path):
    question_path = tmp_path / "questions.json"
    question_path.write_text(
        json.dumps([
            {
                "id": "q1",
                "category": "exact",
                "question": "Điều nào?",
                "expected_article_codes": ["20.2.LQ.1"],
            }
        ], ensure_ascii=False),
        encoding="utf-8",
    )
    questions = load_questions(question_path)
    chunks = [{
        "chunk_id": "one",
        "content": "Nội dung",
        "article_code": "20.2.LQ.1",
    }]
    validate_ground_truth(chunks, questions)

    questions[0]["expected_article_codes"] = ["20.2.LQ.999"]
    with pytest.raises(ProbeInputError, match="missing from chunks"):
        validate_ground_truth(chunks, questions)


def test_chunk_payload_is_compact_and_keeps_legal_metadata():
    chunk = {
        "chunk_id": "one",
        "chunk_key": "article|1",
        "content": "Nội dung",
        "body_text": "Không lưu riêng",
        "article_code": "20.2.LQ.1",
        "clause_number": "2",
        "point_labels": ["a"],
        "token_count": 123,
        "tokenizer_name": "tiktoken:cl100k_base",
    }
    payload = chunk_payload(chunk)
    assert payload["content"] == "Nội dung"
    assert payload["article_code"] == "20.2.LQ.1"
    assert payload["clause_number"] == "2"
    assert payload["point_labels"] == ["a"]
    assert payload["token_count"] == 123
    assert payload["tokenizer_name"] == "tiktoken:cl100k_base"
    assert "body_text" not in payload


def test_truncation_risk_is_reported_as_a_proxy():
    chunks = [
        {
            "token_count": 479,
            "tokenizer_name": "tiktoken:cl100k_base",
        },
        {
            "token_count": 500,
            "tokenizer_name": "tiktoken:cl100k_base",
        },
        {
            "token_count": 512,
            "tokenizer_name": "tiktoken:cl100k_base",
        },
        {},
    ]
    risk = truncation_risk_proxy(chunks, 512)
    assert risk["is_exact"] is False
    assert risk["near_limit_threshold"] == 480
    assert risk["near_or_above_count"] == 2
    assert risk["at_or_above_model_limit_count"] == 1
    assert risk["missing_token_count"] == 1


def test_summarize_metrics():
    metrics = [
        QueryMetric(True, 1.0, 10.0, []),
        QueryMetric(False, 0.0, 20.0, []),
    ]
    summary = summarize(metrics)
    assert summary == {
        "hit_rate_at_k": 0.5,
        "mrr_at_k": 0.5,
        "mean_latency_ms": 15.0,
        "median_latency_ms": 15.0,
    }


def test_bm25_query_uses_query_embed_not_document_embed():
    class DenseEmbedding:
        def tolist(self):
            return [0.0] * 1024

    class DenseModel:
        def embed(self, texts):
            return iter(DenseEmbedding() for _ in texts)

    class SparseEmbedding:
        indices = [1]
        values = [1.0]

    class SparseModel:
        query_called = False

        def embed(self, texts):
            raise AssertionError("document embed must not encode a BM25 query")

        def query_embed(self, texts):
            self.query_called = True
            return iter(SparseEmbedding() for _ in texts)

    class SparseVector:
        def __init__(self, indices, values):
            self.indices = indices
            self.values = values

    class Client:
        def query_points(self, **kwargs):
            point = SimpleNamespace(
                payload={
                    "article_code": "20.2.LQ.1",
                    "content": "Nội dung",
                },
                score=1.0,
            )
            return SimpleNamespace(points=[point])

    args = build_parser().parse_args([])
    sparse_model = SparseModel()
    dense, sparse = retrieve_one(
        Client(),
        SimpleNamespace(SparseVector=SparseVector),
        DenseModel(),
        sparse_model,
        "Câu hỏi",
        {"20.2.LQ.1"},
        args,
    )
    assert sparse_model.query_called is True
    assert dense.hit is True
    assert sparse.hit is True
