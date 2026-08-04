#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

from evaluate_lexical_baseline import (
    BM25Index,
    article_code,
    chunk_id,
    chunk_search_text,
    first_nonempty,
    load_json,
    load_jsonl,
    recall,
    reciprocal_rank,
    sha256_file,
)
from evaluate_dense_hybrid import (
    encode_queries,
    load_or_build_document_embeddings,
)


@dataclass(frozen=True)
class Config:
    strategy: str
    candidate_k: int
    article_cap: int = 0
    selected_articles: int = 0
    chunks_per_article: int = 0
    dense_weight: float = 1.0
    sparse_weight: float = 0.0
    rrf_constant: int = 60

    @property
    def name(self) -> str:
        parts = [self.strategy, f"cand{self.candidate_k}"]
        if "hybrid" in self.strategy:
            parts += [
                f"dw{self.dense_weight:.2f}",
                f"sw{self.sparse_weight:.2f}",
                f"rrf{self.rrf_constant}",
            ]
        if self.strategy.startswith("direct"):
            parts.append(f"cap{self.article_cap}")
        else:
            parts += [
                f"articles{self.selected_articles}",
                f"chunks{self.chunks_per_article}",
            ]
        return "_".join(parts).replace(".", "p")


def dense_rank_all(query_embedding: np.ndarray, docs: np.ndarray):
    scores = docs @ query_embedding
    order = np.argsort(-scores)
    return [(int(i), float(scores[i])) for i in order]


def weighted_rrf(dense, sparse, dense_weight, sparse_weight, rrf_constant, limit):
    scores: dict[int, float] = defaultdict(float)
    best_rank: dict[int, int] = {}
    for weight, ranking in ((dense_weight, dense), (sparse_weight, sparse)):
        if weight <= 0:
            continue
        for rank, (idx, _score) in enumerate(ranking, 1):
            scores[idx] += weight / (rrf_constant + rank)
            best_rank[idx] = min(best_rank.get(idx, rank), rank)
    ordered = sorted(scores, key=lambda i: (-scores[i], best_rank[i], i))
    return [(i, scores[i]) for i in ordered[:limit]]


def base_ranking(config: Config, dense, sparse):
    if config.strategy in {"direct_dense", "two_stage_dense"}:
        return dense[: config.candidate_k]
    return weighted_rrf(
        dense[: config.candidate_k],
        sparse[: config.candidate_k],
        config.dense_weight,
        config.sparse_weight,
        config.rrf_constant,
        config.candidate_k,
    )


def direct_ranking(config: Config, dense, sparse, chunks, final_k):
    output = []
    counts = Counter()
    for idx, score in base_ranking(config, dense, sparse):
        code = article_code(chunks[idx])
        if not code:
            continue
        if config.article_cap > 0 and counts[code] >= config.article_cap:
            continue
        counts[code] += 1
        output.append((idx, score))
        if len(output) >= final_k:
            break
    return output


def two_stage_ranking(config: Config, dense, sparse, chunks, final_k):
    selected = []
    seen = set()
    for idx, _score in base_ranking(config, dense, sparse):
        code = article_code(chunks[idx])
        if not code or code in seen:
            continue
        seen.add(code)
        selected.append(code)
        if len(selected) >= config.selected_articles:
            break

    selected_set = set(selected)
    output = []
    counts = Counter()
    for idx, score in dense:
        code = article_code(chunks[idx])
        if code not in selected_set:
            continue
        if counts[code] >= config.chunks_per_article:
            continue
        counts[code] += 1
        output.append((idx, score))
        if len(output) >= final_k:
            break
    return output


def retrieve(config: Config, dense, sparse, chunks, final_k):
    if config.strategy.startswith("direct"):
        return direct_ranking(config, dense, sparse, chunks, final_k)
    return two_stage_ranking(config, dense, sparse, chunks, final_k)


def metrics(expected_articles, expected_evidence, ranked_articles, ranked_ids):
    actual_articles = {x for x in ranked_articles if x}
    actual_ids = set(ranked_ids)
    return {
        "any_article_hit": float(not expected_articles or bool(expected_articles & actual_articles)),
        "all_article_hit": float(not expected_articles or expected_articles <= actual_articles),
        "article_recall": recall(expected_articles, actual_articles),
        "article_mrr": reciprocal_rank(expected_articles, ranked_articles),
        "any_evidence_hit": float(not expected_evidence or bool(expected_evidence & actual_ids)),
        "all_evidence_hit": float(not expected_evidence or expected_evidence <= actual_ids),
        "evidence_recall": recall(expected_evidence, actual_ids),
        "evidence_mrr": reciprocal_rank(expected_evidence, ranked_ids),
    }


def mean_metrics(rows):
    names = [
        "any_article_hit", "all_article_hit", "article_recall", "article_mrr",
        "any_evidence_hit", "all_evidence_hit", "evidence_recall", "evidence_mrr",
    ]
    if not rows:
        return {name: 0.0 for name in names}
    return {name: statistics.fmean(row[name] for row in rows) for name in names}


def profile_scores(m):
    return {
        "article_score": 0.45*m["all_article_hit"] + 0.35*m["article_recall"] + 0.15*m["article_mrr"] + 0.05*m["any_article_hit"],
        "evidence_score": 0.40*m["all_evidence_hit"] + 0.35*m["evidence_recall"] + 0.15*m["evidence_mrr"] + 0.10*m["any_evidence_hit"],
        "balanced_score": 0.30*m["all_article_hit"] + 0.25*m["article_recall"] + 0.20*m["all_evidence_hit"] + 0.20*m["evidence_recall"] + 0.05*m["article_mrr"],
    }


def evaluate(config, states, chunks, k_values, include_details=False):
    max_k = max(k_values)
    aggregates = {k: [] for k in k_values}
    single, multi, details = [], [], []
    started = time.perf_counter()

    for state in states:
        ranking = retrieve(config, state["dense"], state["sparse"], chunks, max_k)
        ids = [chunk_id(chunks[i]) for i, _s in ranking]
        articles = [article_code(chunks[i]) for i, _s in ranking]
        q_metrics = {}
        for k in k_values:
            value = metrics(state["expected_articles"], state["expected_evidence"], articles[:k], ids[:k])
            aggregates[k].append(value)
            q_metrics[f"at_{k}"] = {n: round(v, 6) for n, v in value.items()}
            if k == 10:
                (single if len(state["expected_articles"]) <= 1 else multi).append(value)

        if include_details:
            details.append({
                "question_id": state["question"].get("id"),
                "question": state["query"],
                "expected_article_codes": sorted(state["expected_articles"]),
                "expected_evidence_chunk_ids": sorted(state["expected_evidence"]),
                "metrics": q_metrics,
                "retrieved": [
                    {
                        "rank": rank,
                        "score": round(float(score), 8),
                        "chunk_id": chunk_id(chunks[idx]),
                        "article_code": article_code(chunks[idx]),
                        "heading": first_nonempty(chunks[idx], ["heading", "article_title", "title"]),
                    }
                    for rank, (idx, score) in enumerate(ranking, 1)
                ],
            })

    summary = {f"at_{k}": {n: round(v, 6) for n, v in mean_metrics(aggregates[k]).items()} for k in k_values}
    at10 = summary["at_10"]
    profiles = profile_scores(at10)
    row = {
        **asdict(config),
        "name": config.name,
        **{n: round(v, 6) for n, v in profiles.items()},
        **{f"{n}_at_10": v for n, v in at10.items()},
        "single_article_all_hit_at_10": round(mean_metrics(single)["all_article_hit"], 6),
        "multi_article_all_hit_at_10": round(mean_metrics(multi)["all_article_hit"], 6),
        "multi_article_recall_at_10": round(mean_metrics(multi)["article_recall"], 6),
        "ranking_time_ms_total": round((time.perf_counter()-started)*1000, 4),
    }
    report = {
        "retriever": {**asdict(config), "name": config.name},
        "summary": summary,
        "stratified_at_10": {
            "single_article": {n: round(v, 6) for n, v in mean_metrics(single).items()},
            "multi_article": {n: round(v, 6) for n, v in mean_metrics(multi).items()},
        },
        "profile_scores": {n: round(v, 6) for n, v in profiles.items()},
        "per_question": details,
    }
    return row, report


def generate_configs(args):
    configs = []
    for ck in args.candidate_k:
        for cap in args.article_cap:
            configs.append(Config("direct_dense", ck, article_cap=cap))
        for dw in args.dense_weight:
            for rrf in args.rrf_constant:
                for cap in args.article_cap:
                    configs.append(Config("direct_weighted_hybrid", ck, article_cap=cap, dense_weight=dw, sparse_weight=1-dw, rrf_constant=rrf))
        for articles in args.selected_articles:
            for chunks_per in args.chunks_per_article:
                configs.append(Config("two_stage_dense", ck, selected_articles=articles, chunks_per_article=chunks_per))
                for dw in args.dense_weight:
                    for rrf in args.rrf_constant:
                        configs.append(Config("two_stage_weighted_hybrid", ck, selected_articles=articles, chunks_per_article=chunks_per, dense_weight=dw, sparse_weight=1-dw, rrf_constant=rrf))
    return list({c.name: c for c in configs}.values())


def sort_key(row, profile):
    return (
        row[f"{profile}_score"],
        row["all_article_hit_at_10"],
        row["article_recall_at_10"],
        row["all_evidence_hit_at_10"],
        row["evidence_recall_at_10"],
        row["article_mrr_at_10"],
    )


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--golden",
        type=Path,
        default=Path(
            "data/evaluation/golden_questions_v3_unified_candidate.json"
        ),
    )
    p.add_argument(
        "--chunks",
        type=Path,
        default=Path(
            "data/releases/labor-law-2026-07-28-candidate/chunks.jsonl"
        ),
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/evaluation/tuning-unified"),
    )
    p.add_argument("--embedding-cache-dir", type=Path, default=Path("data/evaluation/cache/embeddings"))
    p.add_argument("--model", default="intfloat/multilingual-e5-large")
    p.add_argument("--device", default="cpu")
    p.add_argument("--batch-size", type=int, default=32)
    p.add_argument("--candidate-k", type=int, nargs="+", default=[20, 50])
    p.add_argument("--article-cap", type=int, nargs="+", default=[0, 1, 2])
    p.add_argument("--dense-weight", type=float, nargs="+", default=[0.6, 0.7, 0.8, 0.9])
    p.add_argument("--rrf-constant", type=int, nargs="+", default=[30, 60])
    p.add_argument("--selected-articles", type=int, nargs="+", default=[8, 10, 12])
    p.add_argument("--chunks-per-article", type=int, nargs="+", default=[1, 2, 3])
    p.add_argument("--k", type=int, nargs="+", default=[5, 10, 20])
    return p.parse_args()


def main():
    args = parse_args()
    if 10 not in args.k:
        raise ValueError("--k must include 10")

    golden = load_json(args.golden)
    questions = [q for q in golden.get("questions", golden) if q.get("benchmark_enabled", True)]
    chunks = load_jsonl(args.chunks)
    texts = [chunk_search_text(c) for c in chunks]
    queries = [first_nonempty(q, ["question", "query", "text"]) for q in questions]

    model = SentenceTransformer(args.model, device=args.device)
    corpus_sha = sha256_file(args.chunks)
    doc_embeddings, cache_path, cache_hit = load_or_build_document_embeddings(
        model=model,
        model_name=args.model,
        texts=texts,
        corpus_sha256=corpus_sha,
        cache_dir=args.embedding_cache_dir,
        batch_size=args.batch_size,
    )
    query_embeddings = encode_queries(model, queries, batch_size=args.batch_size)
    bm25 = BM25Index(texts)

    print("Precomputing dense and BM25 rankings...")
    states = []
    for i, q in enumerate(questions):
        states.append({
            "question": q,
            "query": queries[i],
            "expected_articles": {str(x) for x in q.get("expected_article_codes", []) if x},
            "expected_evidence": {str(x) for x in q.get("evidence_chunk_ids", []) if x},
            "dense": dense_rank_all(query_embeddings[i], doc_embeddings),
            "sparse": bm25.rank(queries[i], len(chunks)),
        })

    configs = generate_configs(args)
    print(f"Evaluating {len(configs)} configurations...")
    rows = []
    for index, config in enumerate(configs, 1):
        row, _ = evaluate(config, states, chunks, sorted(set(args.k)), False)
        rows.append(row)
        if index == 1 or index % 25 == 0 or index == len(configs):
            print(f"[{index}/{len(configs)}] {config.name} balanced={row['balanced_score']:.4f}")

    ranked = {
        profile: sorted(rows, key=lambda r: sort_key(r, profile), reverse=True)
        for profile in ("balanced", "article", "evidence")
    }
    best = {profile: values[0] for profile, values in ranked.items()}
    config_map = {c.name: c for c in configs}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "retrieval_grid_results.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(ranked["balanced"][0].keys()))
        writer.writeheader(); writer.writerows(ranked["balanced"])
    (args.output_dir / "retrieval_grid_results.json").write_text(json.dumps(ranked["balanced"], ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

    metadata = {
        "schema_version": "retrieval-tuning-selection-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warning": "Selected on the 45-question development set. Validate once on a locked holdout before final claims.",
        "model": args.model,
        "golden": {"path": str(args.golden), "sha256": sha256_file(args.golden), "question_count": len(questions)},
        "corpus": {"path": str(args.chunks), "sha256": corpus_sha, "chunk_count": len(chunks)},
        "embedding_cache": {"path": str(cache_path), "cache_hit": cache_hit},
        "config_count": len(configs),
        "best": best,
    }
    (args.output_dir / "best_retrieval_configs.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

    for name in {row["name"] for row in best.values()}:
        _row, report = evaluate(config_map[name], states, chunks, sorted(set(args.k)), True)
        report.update({"schema_version": "retrieval-benchmark-v1", "generated_at": datetime.now(timezone.utc).isoformat(), "golden": metadata["golden"], "corpus": metadata["corpus"], "model": args.model})
        (args.output_dir / f"{name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

    lines = [
        "# Retrieval tuning summary", "",
        "> Development-set selection only. Validate on a separate holdout.", "",
        f"- Configurations: {len(configs)}", f"- Model: `{args.model}`", f"- Golden questions: {len(questions)}", "",
    ]
    for profile in ("balanced", "article", "evidence"):
        lines += [f"## Best {profile}", "", f"`{best[profile]['name']}`", "", "```json", json.dumps(best[profile], ensure_ascii=False, indent=2), "```", ""]
    (args.output_dir / "retrieval_tuning_summary.md").write_text("\n".join(lines), encoding="utf-8")

    print("\nBEST BALANCED\n", json.dumps(best["balanced"], ensure_ascii=False, indent=2))
    print("\nBEST ARTICLE\n", json.dumps(best["article"], ensure_ascii=False, indent=2))
    print("\nBEST EVIDENCE\n", json.dumps(best["evidence"], ensure_ascii=False, indent=2))
    print(f"\nOutput: {args.output_dir}")


if __name__ == "__main__":
    main()
