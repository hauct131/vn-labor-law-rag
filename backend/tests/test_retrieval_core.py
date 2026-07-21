"""Unit tests for dense, Vietnamese sparse, and hybrid retrieval."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.app.retrieval.dense_component import (
    DenseRetriever,
    e5_query_text,
)
from backend.app.retrieval.hybrid_retriever import (
    HybridRetriever,
    reciprocal_rank_fusion,
)
from backend.app.retrieval.models import RetrievalHit
from backend.app.retrieval.retriever_factory import get_retriever
from backend.app.retrieval.sparse_retriever import (
    VnCoreNlpBm25Retriever,
    load_legal_chunks,
)


class FakeSegmenter:
    def word_segment(self, text: str) -> list[str]:
        replacements = {
            "bất hợp pháp": "bất_hợp_pháp",
            "đình công": "đình_công",
            "thời giờ": "thời_giờ",
        }
        segmented = text
        for source, target in replacements.items():
            segmented = segmented.replace(source, target)
        return [segmented]


def make_hit(
    chunk_id: str,
    score: float,
    rank: int,
    origin: str,
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        content=f"Nội dung {chunk_id}",
        score=score,
        rank=rank,
        retrieval_origin=origin,
        payload={"chunk_id": chunk_id, "content": f"Nội dung {chunk_id}"},
    )


class StubRetriever:
    def __init__(self, hits: list[RetrievalHit]) -> None:
        self.hits = hits
        self.calls: list[tuple[str, int | None]] = []

    def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
    ) -> list[RetrievalHit]:
        self.calls.append((query, top_k))
        return self.hits[:top_k]


class RetrievalCoreTests(unittest.TestCase):
    def test_e5_query_prefix_is_applied_once(self) -> None:
        model = "intfloat/multilingual-e5-large"
        self.assertEqual(e5_query_text("Câu hỏi", model), "query: Câu hỏi")
        self.assertEqual(
            e5_query_text("query: Câu hỏi", model),
            "query: Câu hỏi",
        )
        self.assertEqual(e5_query_text("Câu hỏi", "other"), "Câu hỏi")

    def test_dense_retriever_queries_named_vector_and_keeps_payload(self) -> None:
        class FakeEmbeddingModel:
            def __init__(self) -> None:
                self.inputs: list[list[str]] = []

            def embed(self, texts: list[str]):
                self.inputs.append(texts)
                return iter([[0.1, 0.2, 0.3]])

        class FakeClient:
            def __init__(self) -> None:
                self.kwargs = None

            def query_points(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(points=[SimpleNamespace(
                    id="qdrant-id",
                    score=0.91,
                    payload={
                        "chunk_id": "chunk-1",
                        "content": "Điều luật cần tìm",
                        "article_code": "20.2.LQ.204",
                        "_index_corpus_sha256": "abc123",
                    },
                )])

        model = FakeEmbeddingModel()
        client = FakeClient()
        retriever = DenseRetriever(
            qdrant_url="http://localhost:6333",
            collection_name="labor_law",
            model_name="intfloat/multilingual-e5-large",
            vector_name="dense",
            vector_size=3,
            expected_corpus_sha256="abc123",
            client=client,
            embedding_model=model,
        )

        hits = retriever.retrieve("Đình công bất hợp pháp?", top_k=3)

        self.assertEqual(
            model.inputs,
            [["query: Đình công bất hợp pháp?"]],
        )
        self.assertEqual(client.kwargs["using"], "dense")
        self.assertEqual(client.kwargs["limit"], 3)
        self.assertTrue(client.kwargs["with_payload"])
        self.assertFalse(client.kwargs["with_vectors"])
        self.assertEqual(hits[0].chunk_id, "chunk-1")
        self.assertEqual(hits[0].payload["article_code"], "20.2.LQ.204")
        self.assertEqual(hits[0].retrieval_origin, "dense_e5")

    def test_sparse_retriever_ranks_compound_vietnamese_terms(self) -> None:
        chunks = [
            {
                "chunk_id": "strike",
                "content": "Các trường hợp đình công bất hợp pháp.",
                "article_code": "20.2.LQ.204",
            },
            {
                "chunk_id": "hours",
                "content": "Quy định về thời giờ làm việc bình thường.",
                "article_code": "20.2.LQ.105",
            },
        ]
        retriever = VnCoreNlpBm25Retriever.from_chunks(
            chunks,
            segmenter=FakeSegmenter(),
        )

        hits = retriever.retrieve(
            "Trường hợp đình công bất hợp pháp",
            top_k=2,
        )

        self.assertEqual(hits[0].chunk_id, "strike")
        self.assertGreater(hits[0].score, 0)
        self.assertEqual(
            hits[0].retrieval_origin,
            "sparse_vncorenlp_bm25",
        )
        self.assertEqual(hits[0].payload["article_code"], "20.2.LQ.204")

    def test_local_corpus_loader_checks_count_and_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            corpus = Path(temp_dir) / "chunks.jsonl"
            raw = json.dumps({
                "chunk_id": "one",
                "content": "Nội dung",
            }, ensure_ascii=False) + "\n"
            corpus.write_text(raw, encoding="utf-8")
            expected_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()

            chunks, actual_sha = load_legal_chunks(
                corpus,
                expected_chunks=1,
                expected_sha256=expected_sha,
            )

            self.assertEqual(len(chunks), 1)
            self.assertEqual(actual_sha, expected_sha)

    def test_rrf_rewards_chunks_found_by_both_components(self) -> None:
        sparse = [
            make_hit("shared", 9.0, 1, "sparse"),
            make_hit("sparse-only", 8.0, 2, "sparse"),
        ]
        dense = [
            make_hit("dense-only", 0.95, 1, "dense"),
            make_hit("shared", 0.90, 2, "dense"),
        ]

        fused = reciprocal_rank_fusion(
            {"sparse": sparse, "dense": dense},
            top_k=3,
            rrf_k=60,
        )

        self.assertEqual(fused[0].chunk_id, "shared")
        self.assertEqual(fused[0].retrieval_origin, "hybrid_rrf")
        self.assertEqual(
            fused[0].component_ranks,
            {"sparse": 1, "dense": 2},
        )
        self.assertEqual(
            fused[0].component_scores,
            {"sparse": 9.0, "dense": 0.90},
        )

    def test_hybrid_requests_candidate_pool_then_returns_top_k(self) -> None:
        sparse = StubRetriever([
            make_hit("a", 3.0, 1, "sparse"),
            make_hit("b", 2.0, 2, "sparse"),
        ])
        dense = StubRetriever([
            make_hit("b", 0.9, 1, "dense"),
            make_hit("c", 0.8, 2, "dense"),
        ])
        hybrid = HybridRetriever(
            sparse_retriever=sparse,
            dense_retriever=dense,
            candidate_k=8,
            default_top_k=2,
        )

        hits = hybrid.retrieve("  câu hỏi  ", top_k=2)

        self.assertEqual(sparse.calls, [("câu hỏi", 8)])
        self.assertEqual(dense.calls, [("câu hỏi", 8)])
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].chunk_id, "b")

    def test_factory_accepts_injected_retrievers_without_loading_models(self) -> None:
        sparse = StubRetriever([])
        dense = StubRetriever([])
        self.assertIs(
            get_retriever("sparse", sparse_retriever=sparse),
            sparse,
        )
        self.assertIs(
            get_retriever("dense", dense_retriever=dense),
            dense,
        )


if __name__ == "__main__":
    unittest.main()
