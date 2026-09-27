from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.services.contract_file_extraction import extract_contract
from app.services.contract_review_service import (
    CATEGORIES,
    _excerpt_for,
    _paragraphs,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate Legacy V1 clause retrieval against "
            "a frozen benchmark directory."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help=(
            "Benchmark directory containing contracts/, "
            "sections_for_annotation.json, qrels_gold_v1.json, "
            "and their SHA-256 files."
        ),
    )
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as source:
        for block in iter(lambda: source.read(65536), b""):
            digest.update(block)

    return digest.hexdigest()


def expected_sha(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()

    if not text:
        raise RuntimeError(f"Empty SHA file: {path}")

    return text.split()[0]


def verify_frozen_file(
    path: Path,
    sha_file: Path,
) -> str:
    expected = expected_sha(sha_file)
    actual = sha256(path)

    if actual != expected:
        raise RuntimeError(
            f"Frozen file SHA mismatch:\n"
            f"  file: {path}\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}"
        )

    return actual


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def section_text(section: dict[str, Any]) -> str:
    """
    Reconstruct canonical section text while tolerating either:
    - exported `text`
    - heading + blocks
    """

    direct = section.get("text")

    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    parts: list[str] = []

    heading = section.get("heading")

    if isinstance(heading, str) and heading.strip():
        parts.append(heading.strip())

    for block in section.get("blocks", []):
        if isinstance(block, str):
            text = block
        elif isinstance(block, dict):
            text = str(block.get("text") or "")
        else:
            text = str(block)

        text = text.strip()

        if text:
            parts.append(text)

    return "\n".join(parts)


def map_excerpt_to_section(
    excerpt: str,
    sections: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Map the exact Legacy V1 excerpt back to one frozen canonical section.

    This intentionally avoids semantic/fuzzy retrieval.

    Expected cases:
    1. _excerpt_for returns complete/truncated section
       → excerpt is contained in canonical section.
    2. _excerpt_for returns one paragraph
       → paragraph is contained in one canonical section.

    Ambiguous mappings fail loudly instead of silently choosing one.
    """

    if not excerpt.strip():
        return None

    excerpt_norm = normalize(excerpt)

    exact_candidates: list[
        tuple[int, dict[str, Any]]
    ] = []

    for section in sections:
        canonical = normalize(section_text(section))

        if not canonical:
            continue

        if excerpt_norm in canonical:
            exact_candidates.append(
                (len(excerpt_norm), section)
            )
        elif canonical in excerpt_norm:
            exact_candidates.append(
                (len(canonical), section)
            )

    if exact_candidates:
        best_length = max(
            length
            for length, _section in exact_candidates
        )

        best = [
            section
            for length, section in exact_candidates
            if length == best_length
        ]

        if len(best) == 1:
            return best[0]

        ids = [
            section.get("section_id")
            for section in best
        ]

        raise RuntimeError(
            "Ambiguous exact section mapping: "
            f"{ids}\nExcerpt:\n{excerpt}"
        )

    # Conservative fallback:
    # find an excerpt line that occurs in exactly one section.
    for line in excerpt.splitlines():
        line_norm = normalize(line)

        if len(line_norm) < 12:
            continue

        matches = [
            section
            for section in sections
            if line_norm in normalize(section_text(section))
        ]

        if len(matches) == 1:
            return matches[0]

    raise RuntimeError(
        "Could not map Legacy V1 excerpt to canonical section.\n"
        f"Excerpt:\n{excerpt}"
    )


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
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower

    return (
        ordered[lower] * (1 - fraction)
        + ordered[upper] * fraction
    )


def main() -> None:
    args = parse_args()
    root = args.root.resolve()

    contracts_dir = root / "contracts"
    sections_path = root / "sections_for_annotation.json"
    qrels_path = root / "qrels_gold_v1.json"
    sections_sha_path = root / "SECTIONS_SHA256.txt"
    qrels_sha_path = root / "QRELS_GOLD_V1_SHA256.txt"
    results_dir = root / "results"
    results_path = results_dir / "legacy_v1.json"
    benchmark_name = f"{root.name}_gold_v1"

    sections_sha = verify_frozen_file(
        sections_path,
        sections_sha_path,
    )

    qrels_sha = verify_frozen_file(
        qrels_path,
        qrels_sha_path,
    )

    sections_data = json.loads(
        sections_path.read_text(encoding="utf-8")
    )

    qrels = json.loads(
        qrels_path.read_text(encoding="utf-8")
    )

    sections_by_document = {
        doc["document_id"]: doc["sections"]
        for doc in sections_data["documents"]
    }

    rules = {
        rule.key: rule
        for rule in CATEGORIES
    }

    expected_categories = {
        "probation",
        "salary",
        "working_time",
        "termination",
    }

    if set(rules) != expected_categories:
        raise RuntimeError(
            f"Unexpected production categories: {set(rules)}"
        )

    # Extract each contract exactly once using production extraction.
    extracted_by_document: dict[str, str] = {}
    paragraphs_by_document: dict[str, list[str]] = {}
    extraction_latency_ms: dict[str, float] = {}

    document_files: dict[str, str] = {}

    for query in qrels["queries"]:
        document_files[query["document_id"]] = query["file"]

    for document_id, filename in document_files.items():
        path = contracts_dir / Path(filename).name

        if not path.exists():
            raise FileNotFoundError(path)

        started = time.perf_counter_ns()

        extracted = extract_contract(
            path.name,
            None,
            path.read_bytes(),
        )

        paragraphs = _paragraphs(extracted.text)

        elapsed_ms = (
            time.perf_counter_ns() - started
        ) / 1_000_000

        extracted_by_document[document_id] = extracted.text
        paragraphs_by_document[document_id] = paragraphs
        extraction_latency_ms[document_id] = elapsed_ms

    results: list[dict[str, Any]] = []
    retrieval_latencies: list[float] = []

    for query in qrels["queries"]:
        query_id = query["query_id"]
        document_id = query["document_id"]
        category = query["category"]

        rule = rules[category]
        paragraphs = paragraphs_by_document[document_id]

        started = time.perf_counter_ns()

        excerpt = _excerpt_for(
            rule,
            paragraphs,
        )

        latency_ms = (
            time.perf_counter_ns() - started
        ) / 1_000_000

        retrieval_latencies.append(latency_ms)

        predicted_section = map_excerpt_to_section(
            excerpt,
            sections_by_document[document_id],
        )

        predicted_section_id = (
            predicted_section["section_id"]
            if predicted_section is not None
            else None
        )

        predicted_section_index = (
            predicted_section["section_index"]
            if predicted_section is not None
            else None
        )

        judgment_by_section = {
            judgment["section_id"]: judgment
            for judgment in query["judgments"]
        }

        if predicted_section_id is None:
            relevance = 0
        else:
            if predicted_section_id not in judgment_by_section:
                raise RuntimeError(
                    f"Predicted section missing from qrels: "
                    f"{query_id} -> {predicted_section_id}"
                )

            relevance = judgment_by_section[
                predicted_section_id
            ]["relevance"]

        results.append(
            {
                "query_id": query_id,
                "document_id": document_id,
                "category": category,
                "gold_has_relevant": query["has_relevant"],
                "gold_has_primary": query["has_primary"],
                "predicted_section_id": predicted_section_id,
                "predicted_section_index": predicted_section_index,
                "relevance": relevance,
                "returned_result": bool(excerpt),
                "latency_ms": round(latency_ms, 6),
                "excerpt": excerpt,
            }
        )

    if len(results) != len(qrels["queries"]):
        raise RuntimeError(
            "Evaluation result count does not match qrels: "
            f"{len(results)} != {len(qrels['queries'])}"
        )

    positive_queries = [
        result
        for result in results
        if result["gold_has_primary"]
    ]

    no_relevant_queries = [
        result
        for result in results
        if not result["gold_has_relevant"]
    ]

    strict_hits = sum(
        result["relevance"] == 3
        for result in positive_queries
    )

    relevant_hits = sum(
        result["relevance"] >= 2
        for result in positive_queries
    )

    any_positive_hits = sum(
        result["relevance"] >= 1
        for result in positive_queries
    )

    abstention_hits = sum(
        not result["returned_result"]
        for result in no_relevant_queries
    )

    false_positive_no_relevant = sum(
        result["returned_result"]
        for result in no_relevant_queries
    )

    label_distribution = Counter(
        result["relevance"]
        for result in results
    )

    summary = {
        "system": "legacy_v1",
        "queries": len(results),
        "primary_answerable_queries": len(
            positive_queries
        ),
        "no_relevant_queries": len(
            no_relevant_queries
        ),
        "strict_hit_at_1": (
            strict_hits / len(positive_queries)
            if positive_queries
            else 0.0
        ),
        "relevant_hit_at_1": (
            relevant_hits / len(positive_queries)
            if positive_queries
            else 0.0
        ),
        "any_positive_hit_at_1": (
            any_positive_hits / len(positive_queries)
            if positive_queries
            else 0.0
        ),
        "abstention_accuracy_no_relevant": (
            abstention_hits / len(no_relevant_queries)
            if no_relevant_queries
            else 0.0
        ),
        "false_positive_no_relevant": (
            false_positive_no_relevant
        ),
        "mean_top1_relevance_all_queries": (
            sum(
                result["relevance"]
                for result in results
            )
            / len(results)
        ),
        "label_distribution": {
            str(label): label_distribution[label]
            for label in range(4)
        },
        "retrieval_latency_ms": {
            "mean": statistics.fmean(
                retrieval_latencies
            ),
            "p50": percentile(
                retrieval_latencies,
                0.50,
            ),
            "p95": percentile(
                retrieval_latencies,
                0.95,
            ),
            "max": max(retrieval_latencies),
        },
        "document_extraction_latency_ms": {
            "mean": statistics.fmean(
                extraction_latency_ms.values()
            ),
            "p50": percentile(
                list(extraction_latency_ms.values()),
                0.50,
            ),
            "p95": percentile(
                list(extraction_latency_ms.values()),
                0.95,
            ),
        },
    }

    failures = [
        result
        for result in results
        if (
            (
                result["gold_has_primary"]
                and result["relevance"] != 3
            )
            or (
                not result["gold_has_relevant"]
                and result["returned_result"]
            )
        )
    ]

    output = {
        "schema_version": (
            "contract-clause-retrieval-result-v1"
        ),
        "benchmark": benchmark_name,
        "frozen_inputs": {
            "sections_sha256": sections_sha,
            "qrels_sha256": qrels_sha,
        },
        "summary": summary,
        "queries": results,
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
    print("Legacy V1 Clause Retrieval")
    print("==========================")
    print(
        f"Queries:                  "
        f"{summary['queries']}"
    )
    print(
        f"Answerable (primary):     "
        f"{summary['primary_answerable_queries']}"
    )
    print(
        f"No-relevant queries:      "
        f"{summary['no_relevant_queries']}"
    )
    print()

    print(
        f"Strict Hit@1 (=3):        "
        f"{summary['strict_hit_at_1']:.4f} "
        f"({strict_hits}/{len(positive_queries)})"
    )

    print(
        f"Relevant Hit@1 (>=2):     "
        f"{summary['relevant_hit_at_1']:.4f} "
        f"({relevant_hits}/{len(positive_queries)})"
    )

    print(
        f"Any-positive Hit@1 (>=1): "
        f"{summary['any_positive_hit_at_1']:.4f} "
        f"({any_positive_hits}/{len(positive_queries)})"
    )

    print(
        f"Abstention accuracy:      "
        f"{summary['abstention_accuracy_no_relevant']:.4f} "
        f"({abstention_hits}/{len(no_relevant_queries)})"
    )

    print(
        f"False-positive no-rel:    "
        f"{false_positive_no_relevant}"
    )

    print(
        f"Mean top1 relevance:      "
        f"{summary['mean_top1_relevance_all_queries']:.4f}"
    )

    print()
    print("Top1 relevance distribution:")

    for label in range(4):
        print(
            f"  relevance={label}: "
            f"{label_distribution[label]}"
        )

    latency = summary["retrieval_latency_ms"]

    print()
    print("Clause retrieval latency:")
    print(
        f"  mean: {latency['mean']:.4f} ms"
    )
    print(
        f"  p50:  {latency['p50']:.4f} ms"
    )
    print(
        f"  p95:  {latency['p95']:.4f} ms"
    )
    print(
        f"  max:  {latency['max']:.4f} ms"
    )

    print()
    print(
        f"Failures / non-primary predictions: "
        f"{len(failures)}"
    )

    for result in failures:
        print()
        print(
            f"- {result['query_id']}"
        )
        print(
            f"  predicted: "
            f"{result['predicted_section_id']}"
        )
        print(
            f"  relevance: "
            f"{result['relevance']}"
        )
        print(
            f"  excerpt: "
            f"{normalize(result['excerpt'])[:240]}"
        )

    print()
    print(f"Saved: {results_path}")


if __name__ == "__main__":
    main()
