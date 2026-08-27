#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

VALID = {0, 1, 2}
LABEL_NAME = {0: "irrelevant", 1: "supporting", 2: "direct"}


def _safe_rate(n: int, d: int) -> float:
    return round(n / d, 6) if d else 0.0


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    labels = [int(r["manual_label"]) for r in rows]
    c = Counter(labels)
    relevant = c[1] + c[2]
    return {
        "count": n,
        "direct_count": c[2],
        "supporting_count": c[1],
        "irrelevant_count": c[0],
        "direct_rate": _safe_rate(c[2], n),
        "supporting_rate": _safe_rate(c[1], n),
        "semantic_relevant_rate": _safe_rate(relevant, n),
        "semantic_irrelevant_rate": _safe_rate(c[0], n),
        "graded_relevance_mean_0_to_2": round(mean(labels), 6) if labels else 0.0,
        "graded_relevance_normalized_0_to_1": round(mean(labels) / 2.0, 6) if labels else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Score manually labelled Contract Review source relevance audit.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    invalid: list[dict[str, str]] = []
    labelled: list[dict[str, Any]] = []

    for row in rows:
        raw = str(row.get("manual_label") or "").strip()
        try:
            label = int(raw)
        except ValueError:
            invalid.append({"audit_id": row.get("audit_id", ""), "manual_label": raw})
            continue
        if label not in VALID:
            invalid.append({"audit_id": row.get("audit_id", ""), "manual_label": raw})
            continue
        row = dict(row)
        row["manual_label"] = label
        row["is_in_minimum_expected_set"] = int(str(row.get("is_in_minimum_expected_set") or "0"))
        row["source_rank"] = int(str(row.get("source_rank") or "0"))
        labelled.append(row)

    if invalid or len(labelled) != len(rows):
        print(
            json.dumps(
                {
                    "status": "incomplete_or_invalid",
                    "total_rows": len(rows),
                    "valid_labelled_rows": len(labelled),
                    "remaining_or_invalid_rows": len(rows) - len(labelled),
                    "examples": invalid[:20],
                    "required_labels": {"2": "Direct", "1": "Supporting", "0": "Irrelevant"},
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(2)

    overall = _summarize(labelled)
    expected_rows = [r for r in labelled if r["is_in_minimum_expected_set"] == 1]
    outside_rows = [r for r in labelled if r["is_in_minimum_expected_set"] == 0]

    by_category: dict[str, Any] = {}
    categories = sorted({str(r.get("category") or "") for r in labelled})
    for category in categories:
        by_category[category] = _summarize([r for r in labelled if r.get("category") == category])

    by_rank: dict[str, Any] = {}
    for rank in sorted({int(r["source_rank"]) for r in labelled}):
        by_rank[str(rank)] = _summarize([r for r in labelled if int(r["source_rank"]) == rank])

    cases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in labelled:
        cases[str(row.get("case_id") or "")].append(row)

    case_binary_precisions = []
    case_graded_precisions = []
    case_has_direct = []
    case_has_relevant = []
    for case_rows in cases.values():
        labels = [int(r["manual_label"]) for r in case_rows]
        case_binary_precisions.append(sum(x >= 1 for x in labels) / len(labels))
        case_graded_precisions.append(sum(labels) / (2 * len(labels)))
        case_has_direct.append(any(x == 2 for x in labels))
        case_has_relevant.append(any(x >= 1 for x in labels))

    case_level = {
        "case_count": len(cases),
        "mean_semantic_precision_at_k": round(mean(case_binary_precisions), 6),
        "mean_graded_precision_at_k": round(mean(case_graded_precisions), 6),
        "case_has_direct_source_rate": round(mean(case_has_direct), 6),
        "case_has_relevant_source_rate": round(mean(case_has_relevant), 6),
    }

    strict_precision = _safe_rate(len(expected_rows), len(labelled))
    strict_outside_expected = round(1.0 - strict_precision, 6)

    report = {
        "schema_version": "contract-review-source-relevance-audit-v1",
        "rubric": {
            "2": "Direct: source directly governs or is a primary legal basis for the clause/scenario.",
            "1": "Supporting: source materially supports interpretation or analysis but is not the primary required basis.",
            "0": "Irrelevant: source does not materially help evaluate this clause/scenario.",
        },
        "overall": overall,
        "case_level": case_level,
        "strict_expected_set_metrics": {
            "strict_precision_at_runtime_k": strict_precision,
            "outside_expected_rate": strict_outside_expected,
            "note": "Outside-expected is not semantic irrelevance; compare it with manual semantic_irrelevant_rate.",
        },
        "manual_relevance_for_minimum_expected_sources": _summarize(expected_rows),
        "manual_relevance_for_outside_expected_sources": _summarize(outside_rows),
        "by_category": by_category,
        "by_rank": by_rank,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "source_relevance_audit_metrics.json"
    md_path = args.output_dir / "benchmark_summary.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    outside = report["manual_relevance_for_outside_expected_sources"]
    lines = [
        "# Contract Review Manual Source Relevance Audit",
        "",
        f"Labelled source-case pairs: **{overall['count']}**.",
        "",
        "Rubric: **2 = Direct**, **1 = Supporting**, **0 = Irrelevant**.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Strict Precision@runtime-K | {strict_precision:.4f} |",
        f"| Outside-Expected Rate | {strict_outside_expected:.4f} |",
        f"| Semantic Relevant Rate (Direct + Supporting) | {overall['semantic_relevant_rate']:.4f} |",
        f"| Semantic Irrelevant Rate | {overall['semantic_irrelevant_rate']:.4f} |",
        f"| Direct Rate | {overall['direct_rate']:.4f} |",
        f"| Supporting Rate | {overall['supporting_rate']:.4f} |",
        f"| Graded Relevance (0..1) | {overall['graded_relevance_normalized_0_to_1']:.4f} |",
        f"| Mean Semantic Precision@K across cases | {case_level['mean_semantic_precision_at_k']:.4f} |",
        "",
        "## Sources outside the minimum expected set",
        "",
        f"- Count: **{outside['count']}**",
        f"- Still semantically relevant (Direct + Supporting): **{outside['semantic_relevant_rate']:.4f}**",
        f"- Actually irrelevant by manual audit: **{outside['semantic_irrelevant_rate']:.4f}**",
        "",
        "> Outside-Expected Rate and Semantic Irrelevant Rate answer different questions. The former is exact-set membership; the latter is based on manual relevance judgement.",
        "",
        "## By category",
        "",
        "| Category | Relevant | Irrelevant | Direct | Supporting |",
        "|---|---:|---:|---:|---:|",
    ]
    for category, metrics in by_category.items():
        lines.append(
            f"| {category} | {metrics['semantic_relevant_rate']:.4f} | "
            f"{metrics['semantic_irrelevant_rate']:.4f} | {metrics['direct_rate']:.4f} | "
            f"{metrics['supporting_rate']:.4f} |"
        )

    lines += [
        "",
        "## By source rank",
        "",
        "| Rank | Relevant | Irrelevant | Direct |",
        "|---:|---:|---:|---:|",
    ]
    for rank, metrics in by_rank.items():
        lines.append(
            f"| {rank} | {metrics['semantic_relevant_rate']:.4f} | "
            f"{metrics['semantic_irrelevant_rate']:.4f} | {metrics['direct_rate']:.4f} |"
        )

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({"status": "ok", "json": str(json_path), "markdown": str(md_path), **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
