from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import time
from pathlib import Path
from typing import Any

from app.services.contract_review.bm25_retriever import (
    BM25ClauseRetriever,
)
from app.services.contract_review.e5_retriever import (
    E5ClauseRetriever,
)
from app.services.contract_review.hybrid_retriever import (
    HybridClauseRetriever,
)
from app.services.contract_review.models import (
    ContractBlock,
    ContractSection,
)
from app.services.contract_review.rule_retriever import (
    RuleClauseRetriever,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate contract clause retrievers against "
            "a frozen benchmark directory."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help=(
            "Benchmark directory containing sections_for_annotation.json, "
            "qrels_gold_v1.json, and their SHA-256 files."
        ),
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as source:
        for block in iter(
            lambda: source.read(65536),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def expected_sha(path: Path) -> str:
    return (
        path.read_text(encoding="utf-8")
        .strip()
        .split()[0]
    )


def verify_frozen(
    path: Path,
    sha_path: Path,
) -> str:
    expected = expected_sha(sha_path)
    actual = sha256(path)

    if actual != expected:
        raise RuntimeError(
            f"SHA mismatch: {path}\n"
            f"expected={expected}\n"
            f"actual={actual}"
        )

    return actual


def percentile(
    values: list[float],
    p: float,
) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)

    if len(ordered) == 1:
        return ordered[0]

    position = (len(ordered) - 1) * p
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low

    return (
        ordered[low] * (1.0 - fraction)
        + ordered[high] * fraction
    )


def build_section(
    raw: dict[str, Any],
) -> ContractSection:
    blocks: list[ContractBlock] = []

    for fallback_index, raw_block in enumerate(
        raw.get("blocks", [])
    ):
        if isinstance(raw_block, dict):
            text = str(
                raw_block.get("text") or ""
            )

            index = int(
                raw_block.get(
                    "index",
                    fallback_index,
                )
            )

            kind = str(
                raw_block.get("kind")
                or "paragraph"
            )
        else:
            text = str(raw_block)
            index = fallback_index
            kind = "paragraph"

        if text.strip():
            blocks.append(
                ContractBlock(
                    index=index,
                    text=text,
                    kind=kind,
                )
            )

    # Defensive fallback in case an export only contains "text".
    if not blocks:
        text = str(raw.get("text") or "")
        heading = str(
            raw.get("heading") or ""
        ).strip()

        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]

        if (
            heading
            and lines
            and lines[0] == heading
        ):
            lines = lines[1:]

        blocks = [
            ContractBlock(
                index=i,
                text=line,
            )
            for i, line in enumerate(lines)
        ]

    return ContractSection(
        index=int(raw["section_index"]),
        heading=raw.get("heading"),
        blocks=blocks,
    )


def dcg(relevances: list[int]) -> float:
    return sum(
        (2 ** relevance - 1)
        / math.log2(rank + 1)
        for rank, relevance in enumerate(
            relevances,
            start=1,
        )
    )


def evaluate_system(
    *,
    name: str,
    retriever: Any,
    qrels: dict[str, Any],
    sections_by_document: dict[
        str,
        list[ContractSection],
    ],
    section_ids_by_document: dict[
        str,
        dict[int, str],
    ],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    latencies: list[float] = []

    for query in qrels["queries"]:
        document_id = query["document_id"]
        category = query["category"]

        sections = sections_by_document[
            document_id
        ]

        # Ask for the complete positive-score ranking.
        started = time.perf_counter_ns()

        matches = retriever.retrieve(
            sections=sections,
            category=category,
            top_k=len(sections),
        )

        elapsed_ms = (
            time.perf_counter_ns() - started
        ) / 1_000_000

        latencies.append(elapsed_ms)

        id_by_index = section_ids_by_document[
            document_id
        ]

        judgments = {
            judgment["section_id"]:
                judgment["relevance"]
            for judgment in query["judgments"]
        }

        ranking: list[dict[str, Any]] = []

        seen: set[str] = set()

        for rank, match in enumerate(
            matches,
            start=1,
        ):
            section_id = id_by_index[
                match.section.index
            ]

            if section_id in seen:
                raise RuntimeError(
                    f"Duplicate result: "
                    f"{query['query_id']} "
                    f"{section_id}"
                )

            seen.add(section_id)

            ranking.append(
                {
                    "rank": rank,
                    "section_id": section_id,
                    "section_index":
                        match.section.index,
                    "score":
                        float(match.score),
                    "relevance":
                        int(
                            judgments.get(
                                section_id,
                                0,
                            )
                        ),
                }
            )

        top3 = ranking[:3]

        top1_relevance = (
            top3[0]["relevance"]
            if top3
            else 0
        )

        relevant_ids = {
            section_id
            for section_id, relevance
            in judgments.items()
            if relevance >= 2
        }

        retrieved_top3 = {
            item["section_id"]
            for item in top3
        }

        recall_at_3 = (
            len(
                relevant_ids
                & retrieved_top3
            )
            / len(relevant_ids)
            if relevant_ids
            else None
        )

        reciprocal_rank = 0.0

        for item in ranking:
            if item["relevance"] == 3:
                reciprocal_rank = (
                    1.0 / item["rank"]
                )
                break

        actual_rels = [
            item["relevance"]
            for item in top3
        ]

        while len(actual_rels) < 3:
            actual_rels.append(0)

        ideal_rels = sorted(
            judgments.values(),
            reverse=True,
        )[:3]

        while len(ideal_rels) < 3:
            ideal_rels.append(0)

        ideal_dcg = dcg(ideal_rels)

        ndcg_at_3 = (
            dcg(actual_rels) / ideal_dcg
            if ideal_dcg > 0
            else None
        )

        rows.append(
            {
                "query_id":
                    query["query_id"],
                "document_id":
                    document_id,
                "category":
                    category,
                "gold_has_relevant":
                    query["has_relevant"],
                "gold_has_primary":
                    query["has_primary"],
                "returned_result":
                    bool(ranking),
                "top1_relevance":
                    top1_relevance,
                "recall_at_3":
                    recall_at_3,
                "reciprocal_rank_primary":
                    reciprocal_rank,
                "ndcg_at_3":
                    ndcg_at_3,
                "latency_ms":
                    round(elapsed_ms, 6),
                "ranking":
                    ranking,
            }
        )

    answerable = [
        row
        for row in rows
        if row["gold_has_primary"]
    ]

    no_relevant = [
        row
        for row in rows
        if not row["gold_has_relevant"]
    ]

    recall_rows = [
        row
        for row in rows
        if row["recall_at_3"] is not None
    ]

    ndcg_rows = [
        row
        for row in rows
        if row["ndcg_at_3"] is not None
    ]

    strict_hits = sum(
        row["top1_relevance"] == 3
        for row in answerable
    )

    relevant_hits = sum(
        row["top1_relevance"] >= 2
        for row in answerable
    )

    abstentions = sum(
        not row["returned_result"]
        for row in no_relevant
    )

    summary = {
        "system": name,
        "queries": len(rows),
        "answerable_queries":
            len(answerable),
        "no_relevant_queries":
            len(no_relevant),

        "strict_hit_at_1":
            strict_hits / len(answerable),

        "relevant_hit_at_1":
            relevant_hits / len(answerable),

        "recall_at_3":
            statistics.fmean(
                row["recall_at_3"]
                for row in recall_rows
            ),

        "mrr_primary":
            statistics.fmean(
                row[
                    "reciprocal_rank_primary"
                ]
                for row in answerable
            ),

        "ndcg_at_3":
            statistics.fmean(
                row["ndcg_at_3"]
                for row in ndcg_rows
            ),

        "abstention_accuracy_no_relevant":
            (
                abstentions
                / len(no_relevant)
                if no_relevant
                else 0.0
            ),

        "false_positive_no_relevant":
            sum(
                row["returned_result"]
                for row in no_relevant
            ),

        "latency_ms": {
            "mean":
                statistics.fmean(
                    latencies
                ),
            "p50":
                percentile(
                    latencies,
                    0.50,
                ),
            "p95":
                percentile(
                    latencies,
                    0.95,
                ),
            "max":
                max(latencies),
        },
    }

    return {
        "summary": summary,
        "queries": rows,
    }


def main() -> None:
    args = parse_args()
    root = args.root.resolve()

    sections_path = root / "sections_for_annotation.json"
    qrels_path = root / "qrels_gold_v1.json"
    sections_sha_path = root / "SECTIONS_SHA256.txt"
    qrels_sha_path = root / "QRELS_GOLD_V1_SHA256.txt"
    results_dir = root / "results"
    results_path = results_dir / "retrieval_baselines_v1.json"
    benchmark_name = f"{root.name}_gold_v1"

    sections_sha = verify_frozen(
        sections_path,
        sections_sha_path,
    )

    qrels_sha = verify_frozen(
        qrels_path,
        qrels_sha_path,
    )

    sections_data = json.loads(
        sections_path.read_text(
            encoding="utf-8"
        )
    )

    qrels = json.loads(
        qrels_path.read_text(
            encoding="utf-8"
        )
    )

    sections_by_document: dict[
        str,
        list[ContractSection],
    ] = {}

    section_ids_by_document: dict[
        str,
        dict[int, str],
    ] = {}

    for document in sections_data[
        "documents"
    ]:
        document_id = document[
            "document_id"
        ]

        sections_by_document[
            document_id
        ] = [
            build_section(section)
            for section
            in document["sections"]
        ]

        section_ids_by_document[
            document_id
        ] = {
            int(section["section_index"]):
                section["section_id"]
            for section
            in document["sections"]
        }

    # --------------------------------------------------
    # Prepare E5 outside timed per-query retrieval.
    #
    # We report:
    # - document embedding preparation separately
    # - fixed category query embedding preparation separately
    # - warm retrieval latency = cosine scoring + ranking
    #
    # This avoids mixing one-time model/index preparation with
    # online ranking latency.
    # --------------------------------------------------

    all_sections = [
        section
        for sections
        in sections_by_document.values()
        for section in sections
    ]

    categories = sorted({
        query["category"]
        for query in qrels["queries"]
    })

    print()
    print("Loading E5 model...")

    e5 = E5ClauseRetriever()

    started = time.perf_counter_ns()

    e5.prepare(
        all_sections
    )

    e5_document_prepare_ms = (
        time.perf_counter_ns() - started
    ) / 1_000_000

    started = time.perf_counter_ns()

    e5.prepare_queries(
        categories
    )

    e5_query_prepare_ms = (
        time.perf_counter_ns() - started
    ) / 1_000_000

    preparation = {
        "e5": {
            "model":
                e5.model_name,
            "document_count":
                len(all_sections),
            "document_embedding_ms":
                e5_document_prepare_ms,
            "query_count":
                len(categories),
            "query_embedding_ms":
                e5_query_prepare_ms,
            "latency_semantics":
                (
                    "per-query latency is warm retrieval "
                    "after document and fixed category "
                    "query embeddings are prepared"
                ),
        }
    }

    systems = {
        "rule_clause_retriever":
            RuleClauseRetriever(),

        "bm25_phrase":
            BM25ClauseRetriever(),

        "e5_dense":
            e5,

        "hybrid_rrf":
            HybridClauseRetriever(
                bm25=BM25ClauseRetriever(),
                e5=e5,
                rrf_k=60,
                bm25_weight=1.0,
                e5_weight=1.0,
            ),
    }

    results: dict[str, Any] = {}

    for name, retriever in systems.items():
        results[name] = evaluate_system(
            name=name,
            retriever=retriever,
            qrels=qrels,
            sections_by_document=
                sections_by_document,
            section_ids_by_document=
                section_ids_by_document,
        )

    output = {
        "schema_version":
            "contract-clause-retrieval-results-v1",

        "benchmark":
            benchmark_name,

        "frozen_inputs": {
            "sections_sha256":
                sections_sha,
            "qrels_sha256":
                qrels_sha,
        },

        "preparation":
            preparation,

        "systems":
            results,
    }

    results_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    results_path.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print(
        "Contract Clause Retrieval"
    )
    print(
        "========================="
    )

    print()
    print("E5 preparation")
    print("--------------")
    print(
        f"Model:          "
        f"{e5.model_name}"
    )
    print(
        f"Documents:      "
        f"{len(all_sections)}"
    )
    print(
        f"Document embed: "
        f"{e5_document_prepare_ms:.2f} ms"
    )
    print(
        f"Query embed x4: "
        f"{e5_query_prepare_ms:.2f} ms"
    )
    print(
        "E5 latency below is warm "
        "cosine + ranking latency."
    )

    for name, result in results.items():
        s = result["summary"]

        print()
        print(name)
        print("-" * len(name))

        print(
            f"Strict Hit@1:   "
            f"{s['strict_hit_at_1']:.4f}"
        )

        print(
            f"Relevant H@1:   "
            f"{s['relevant_hit_at_1']:.4f}"
        )

        print(
            f"Recall@3:       "
            f"{s['recall_at_3']:.4f}"
        )

        print(
            f"MRR primary:    "
            f"{s['mrr_primary']:.4f}"
        )

        print(
            f"nDCG@3:         "
            f"{s['ndcg_at_3']:.4f}"
        )

        print(
            f"Abstention:     "
            f"{s['abstention_accuracy_no_relevant']:.4f}"
        )

        print(
            f"False-pos none: "
            f"{s['false_positive_no_relevant']}"
        )

        latency = s["latency_ms"]

        print(
            f"Latency mean:   "
            f"{latency['mean']:.4f} ms"
        )

        print(
            f"Latency p95:    "
            f"{latency['p95']:.4f} ms"
        )

        failures = [
            row
            for row in result["queries"]
            if (
                row["gold_has_primary"]
                and row[
                    "top1_relevance"
                ] != 3
            )
        ]

        print(
            f"Top1 failures:  "
            f"{len(failures)}"
        )

        for row in failures:
            predicted = (
                row["ranking"][0][
                    "section_id"
                ]
                if row["ranking"]
                else None
            )

            print(
                f"  {row['query_id']}"
                f" -> {predicted}"
                f" rel="
                f"{row['top1_relevance']}"
            )

    print()
    print(
        f"Saved: {results_path}"
    )


if __name__ == "__main__":
    main()
