#!/usr/bin/env python3
"""Evaluate a lexical BM25 retrieval baseline against a golden dataset.

No external search engine or Python dependency is required.

Example:
    python scripts/evaluate_lexical_baseline.py \
      --golden data/evaluation/golden_questions_v4_canonical_word_candidate.json \
      --chunks data/releases/labor-law-canonical-word-20260804-164432-candidate/canonical_chunks.jsonl \
      --output data/evaluation/results/canonical_word_804/bm25_baseline.json \
      --k 5 10 20
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


TOKEN_RE = re.compile(r"[0-9a-zA-ZÀ-ỹĐđ]+", re.UNICODE)


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return " ".join(TOKEN_RE.findall(text))


def tokenize(value: Any) -> list[str]:
    return normalize_text(value).split()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def first_nonempty(mapping: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = mapping.get(key)
        if value:
            return str(value)
    return ""


def chunk_search_text(chunk: dict[str, Any]) -> str:
    fields = [
        chunk.get("article_code"),
        chunk.get("article_title"),
        chunk.get("heading"),
        chunk.get("title"),
        chunk.get("chunk_type"),
        chunk.get("body_text"),
        chunk.get("content"),
        chunk.get("text"),
    ]
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        fields.extend(
            [
                metadata.get("article_code"),
                metadata.get("article_title"),
                metadata.get("document_number"),
                metadata.get("heading"),
            ]
        )
    return "\n".join(str(value) for value in fields if value)


def chunk_id(chunk: dict[str, Any]) -> str:
    value = chunk.get("chunk_id")
    if value:
        return str(value)
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict) and metadata.get("chunk_id"):
        return str(metadata["chunk_id"])
    raise ValueError("A chunk is missing chunk_id.")


def article_code(chunk: dict[str, Any]) -> str:
    value = chunk.get("article_code")
    if value:
        return str(value)
    metadata = chunk.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("article_code")
        if value:
            return str(value)
    return ""


class BM25Index:
    def __init__(
        self,
        documents: list[str],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        if not documents:
            raise ValueError("Cannot create BM25 index without documents.")

        self.k1 = k1
        self.b = b
        self.term_frequencies = [Counter(tokenize(doc)) for doc in documents]
        self.lengths = [sum(counter.values()) for counter in self.term_frequencies]
        self.avg_length = sum(self.lengths) / len(self.lengths)

        document_frequency: Counter[str] = Counter()
        for counter in self.term_frequencies:
            document_frequency.update(counter.keys())

        count = len(documents)
        self.idf = {
            term: math.log(1.0 + (count - freq + 0.5) / (freq + 0.5))
            for term, freq in document_frequency.items()
        }

    def rank(self, query: str, limit: int) -> list[tuple[int, float]]:
        terms = tokenize(query)
        if not terms:
            return []

        query_frequency = Counter(terms)
        scores: list[tuple[int, float]] = []

        for index, term_frequency in enumerate(self.term_frequencies):
            document_length = self.lengths[index]
            normalization = self.k1 * (
                1.0
                - self.b
                + self.b * document_length / max(self.avg_length, 1e-9)
            )

            score = 0.0
            for term, qf in query_frequency.items():
                frequency = term_frequency.get(term, 0)
                if not frequency:
                    continue
                score += (
                    self.idf.get(term, 0.0)
                    * frequency
                    * (self.k1 + 1.0)
                    / (frequency + normalization)
                    * (1.0 + math.log(qf))
                )

            if score > 0.0:
                scores.append((index, score))

        scores.sort(key=lambda item: (-item[1], item[0]))
        return scores[:limit]


def recall(expected: set[str], actual: set[str]) -> float:
    if not expected:
        return 1.0
    return len(expected & actual) / len(expected)


def reciprocal_rank(expected: set[str], ranked: list[str]) -> float:
    if not expected:
        return 1.0
    for index, value in enumerate(ranked, start=1):
        if value in expected:
            return 1.0 / index
    return 0.0


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path(
            "data/evaluation/"
            "golden_questions_v4_canonical_word_candidate.json"
        ),
    )
    parser.add_argument(
        "--chunks",
        type=Path,
        default=Path(
            "data/releases/"
            "labor-law-canonical-word-20260804-164432-candidate/"
            "canonical_chunks.jsonl"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/evaluation/results/canonical_word_804/bm25_baseline.json"
        ),
    )
    parser.add_argument(
        "--k",
        type=int,
        nargs="+",
        default=[5, 10, 20],
    )
    parser.add_argument("--bm25-k1", type=float, default=1.5)
    parser.add_argument("--bm25-b", type=float, default=0.75)
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Also evaluate questions with benchmark_enabled=false.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    k_values = sorted(set(args.k))
    if not k_values or min(k_values) <= 0:
        raise ValueError("--k values must be positive integers.")

    golden = load_json(args.golden)
    questions = golden.get("questions", golden)
    if not isinstance(questions, list):
        raise ValueError("Golden dataset must contain a questions list.")

    if not args.include_disabled:
        questions = [
            question
            for question in questions
            if question.get("benchmark_enabled", True)
        ]

    chunks = load_jsonl(args.chunks)
    ids = [chunk_id(chunk) for chunk in chunks]
    if len(ids) != len(set(ids)):
        raise ValueError("Chunk corpus contains duplicate chunk IDs.")

    document_texts = [chunk_search_text(chunk) for chunk in chunks]
    index = BM25Index(
        document_texts,
        k1=args.bm25_k1,
        b=args.bm25_b,
    )

    max_k = max(k_values)
    latency_values: list[float] = []
    per_question: list[dict[str, Any]] = []

    aggregate: dict[int, dict[str, list[float]]] = {
        k: {
            "any_article_hit": [],
            "all_article_hit": [],
            "article_recall": [],
            "article_mrr": [],
            "any_evidence_hit": [],
            "all_evidence_hit": [],
            "evidence_recall": [],
            "evidence_mrr": [],
        }
        for k in k_values
    }

    for question in questions:
        query = first_nonempty(question, ["question", "query", "text"])
        if not query:
            raise ValueError(
                f"Question {question.get('id')} has no query text."
            )

        expected_articles = {
            str(value)
            for value in question.get("expected_article_codes", [])
            if value
        }
        expected_evidence = {
            str(value)
            for value in question.get("evidence_chunk_ids", [])
            if value
        }

        started = time.perf_counter()
        ranked = index.rank(query, max_k)
        latency_ms = (time.perf_counter() - started) * 1000.0
        latency_values.append(latency_ms)

        retrieved = []
        for position, (chunk_index, score) in enumerate(ranked, start=1):
            chunk = chunks[chunk_index]
            retrieved.append(
                {
                    "rank": position,
                    "score": round(score, 8),
                    "chunk_id": ids[chunk_index],
                    "article_code": article_code(chunk),
                    "heading": first_nonempty(
                        chunk,
                        ["heading", "article_title", "title"],
                    ),
                }
            )

        question_metrics: dict[str, Any] = {}
        ranked_ids = [item["chunk_id"] for item in retrieved]
        ranked_articles = [item["article_code"] for item in retrieved]

        for k in k_values:
            top_ids = ranked_ids[:k]
            top_articles = ranked_articles[:k]
            top_id_set = set(top_ids)
            top_article_set = set(top_articles)

            article_recall_value = recall(
                expected_articles,
                top_article_set,
            )
            evidence_recall_value = recall(
                expected_evidence,
                top_id_set,
            )

            values = {
                "any_article_hit": float(
                    not expected_articles
                    or bool(expected_articles & top_article_set)
                ),
                "all_article_hit": float(
                    not expected_articles
                    or expected_articles <= top_article_set
                ),
                "article_recall": article_recall_value,
                "article_mrr": reciprocal_rank(
                    expected_articles,
                    top_articles,
                ),
                "any_evidence_hit": float(
                    not expected_evidence
                    or bool(expected_evidence & top_id_set)
                ),
                "all_evidence_hit": float(
                    not expected_evidence
                    or expected_evidence <= top_id_set
                ),
                "evidence_recall": evidence_recall_value,
                "evidence_mrr": reciprocal_rank(
                    expected_evidence,
                    top_ids,
                ),
            }

            for name, value in values.items():
                aggregate[k][name].append(value)

            question_metrics[f"at_{k}"] = {
                name: round(value, 6)
                for name, value in values.items()
            }

        per_question.append(
            {
                "question_id": question.get("id"),
                "question": query,
                "expected_article_codes": sorted(expected_articles),
                "expected_evidence_chunk_ids": sorted(expected_evidence),
                "latency_ms": round(latency_ms, 4),
                "metrics": question_metrics,
                "retrieved": retrieved,
            }
        )

    summary: dict[str, Any] = {}
    for k in k_values:
        metrics = {}
        for name, values in aggregate[k].items():
            metrics[name] = round(statistics.fmean(values), 6)
        summary[f"at_{k}"] = metrics

    failed_at_10 = []
    selected_k = 10 if 10 in k_values else max_k
    for item in per_question:
        metrics = item["metrics"][f"at_{selected_k}"]
        if metrics["all_article_hit"] < 1.0:
            failed_at_10.append(
                {
                    "question_id": item["question_id"],
                    "article_recall": metrics["article_recall"],
                    "expected_article_codes": item[
                        "expected_article_codes"
                    ],
                    "retrieved_article_codes": [
                        result["article_code"]
                        for result in item["retrieved"][:selected_k]
                    ],
                }
            )

    report = {
        "schema_version": "retrieval-benchmark-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "retriever": {
            "name": "pure_python_bm25",
            "k1": args.bm25_k1,
            "b": args.bm25_b,
            "tokenizer": "unicode_word_lowercase",
        },
        "golden": {
            "path": str(args.golden),
            "schema_version": golden.get("schema_version"),
            "sha256": sha256_file(args.golden),
            "question_count": len(questions),
        },
        "corpus": {
            "path": str(args.chunks),
            "sha256": sha256_file(args.chunks),
            "chunk_count": len(chunks),
        },
        "k_values": k_values,
        "summary": summary,
        "latency_ms": {
            "mean": round(statistics.fmean(latency_values), 4),
            "p50": round(percentile(latency_values, 0.50), 4),
            "p95": round(percentile(latency_values, 0.95), 4),
            "max": round(max(latency_values, default=0.0), 4),
        },
        f"questions_missing_all_articles_at_{selected_k}": failed_at_10,
        "per_question": per_question,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("retriever=pure_python_bm25")
    print(f"questions={len(questions)}")
    print(f"chunks={len(chunks)}")
    for key, metrics in summary.items():
        print(
            f"{key}: "
            f"article_any={metrics['any_article_hit']:.4f} "
            f"article_all={metrics['all_article_hit']:.4f} "
            f"article_recall={metrics['article_recall']:.4f} "
            f"article_mrr={metrics['article_mrr']:.4f} "
            f"evidence_any={metrics['any_evidence_hit']:.4f} "
            f"evidence_all={metrics['all_evidence_hit']:.4f} "
            f"evidence_recall={metrics['evidence_recall']:.4f}"
        )
    print(
        "latency_ms: "
        f"mean={report['latency_ms']['mean']:.2f} "
        f"p50={report['latency_ms']['p50']:.2f} "
        f"p95={report['latency_ms']['p95']:.2f}"
    )
    print(f"report={args.output}")


if __name__ == "__main__":
    main()
