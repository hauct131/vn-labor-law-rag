#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from app.services.contract_review_service import (
    CATEGORIES,
    _excerpt_for,
    _paragraphs,
    evidence_retriever,
)


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not rows:
        raise ValueError("Benchmark dataset must contain a non-empty cases list")
    return rows


def safe_div(num: int | float, den: int | float) -> float:
    return float(num / den) if den else 0.0


def case_metrics(retrieved: list[str], expected: list[str]) -> dict[str, float]:
    retrieved_set = set(retrieved)
    expected_set = set(expected)
    matched = retrieved_set & expected_set
    precision = safe_div(len(matched), len(retrieved_set))
    recall = safe_div(len(matched), len(expected_set))
    f1 = safe_div(2 * precision * recall, precision + recall)
    reciprocal_rank = 0.0
    for rank, code in enumerate(retrieved, start=1):
        if code in expected_set:
            reciprocal_rank = 1.0 / rank
            break
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "any_hit": float(bool(matched)),
        "exact_set_match": float(retrieved_set == expected_set),
        "mrr": reciprocal_rank,
        "noise_rate": 1.0 - precision if retrieved_set else 0.0,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    names = ("precision", "recall", "f1", "any_hit", "exact_set_match", "mrr", "noise_rate")
    result = {name: mean(row["metrics"][name] for row in rows) for name in names}
    result["detection_rate"] = mean(float(bool(row["excerpt"])) for row in rows)
    result["sources_per_case"] = mean(len(row["retrieved_article_codes"]) for row in rows)

    total_matched = 0
    total_retrieved = 0
    total_expected = 0
    for row in rows:
        r = set(row["retrieved_article_codes"])
        e = set(row["expected_article_codes"])
        total_matched += len(r & e)
        total_retrieved += len(r)
        total_expected += len(e)
    micro_p = safe_div(total_matched, total_retrieved)
    micro_r = safe_div(total_matched, total_expected)
    result["micro_precision"] = micro_p
    result["micro_recall"] = micro_r
    result["micro_f1"] = safe_div(2 * micro_p * micro_r, micro_p + micro_r)
    return {key: round(value, 6) for key, value in result.items()}


def run_variant(cases: list[dict[str, Any]], *, variant: str, fixed_k: int | None = None) -> dict[str, Any]:
    rules = {rule.key: rule for rule in CATEGORIES}
    retriever = evidence_retriever()
    rows: list[dict[str, Any]] = []

    for case in cases:
        category = str(case["category"])
        rule = rules[category]
        text = str(case["text"])
        excerpt = _excerpt_for(rule, _paragraphs(text))
        query = rule.query + (f" Nội dung hợp đồng: {excerpt[:500]}" if excerpt else "")
        top_k = fixed_k if fixed_k is not None else len(rule.preferred_article_codes)
        sources = retriever.retrieve(
            query,
            top_k=top_k,
            preferred_article_codes=rule.preferred_article_codes,
        )
        retrieved = [source.article_code for source in sources if source.article_code]
        expected = [str(code) for code in case["expected_article_codes"]]
        rows.append({
            "id": case["id"],
            "category": category,
            "text": text,
            "rationale": case.get("rationale"),
            "excerpt": excerpt,
            "top_k": top_k,
            "expected_article_codes": expected,
            "preferred_article_codes": list(rule.preferred_article_codes),
            "retrieved_article_codes": retrieved,
            "metrics": case_metrics(retrieved, expected),
            "blind_spot_expected_codes": sorted(set(expected) - set(rule.preferred_article_codes)),
        })

    by_category: dict[str, Any] = {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["category"]].append(row)
    for category, category_rows in sorted(grouped.items()):
        by_category[category] = aggregate(category_rows)

    return {
        "variant": variant,
        "fixed_k": fixed_k,
        "summary": aggregate(rows),
        "by_category": by_category,
        "cases": rows,
    }


def subset_summary(rows: list[dict[str, Any]], predicate) -> dict[str, float]:
    selected = [row for row in rows if predicate(row)]
    return aggregate(selected) if selected else {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-fixed-k", type=int, default=10)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    variants = [
        run_variant(cases, variant=f"fixed_k_{k}", fixed_k=k)
        for k in range(1, args.max_fixed_k + 1)
    ]
    runtime_current = run_variant(cases, variant="runtime_current", fixed_k=4)
    variants.append(runtime_current)

    fixed = variants[:-1]
    best_fixed = max(
        fixed,
        key=lambda item: (
            item["summary"]["macro_f1"] if "macro_f1" in item["summary"] else item["summary"]["f1"],
            item["summary"]["recall"],
            -int(item["fixed_k"] or 0),
        ),
    )

    # Count deliberately independent cases whose expected article lies outside
    # the current category preferred list.
    blind_spot_cases = []
    for row in runtime_current["cases"]:
        if row["blind_spot_expected_codes"]:
            blind_spot_cases.append({
                "id": row["id"],
                "category": row["category"],
                "expected_outside_preferred": row["blind_spot_expected_codes"],
                "retrieved_article_codes": row["retrieved_article_codes"],
                "recall": row["metrics"]["recall"],
            })

    runtime_in_preferred = subset_summary(
        runtime_current["cases"],
        lambda row: not row["blind_spot_expected_codes"],
    )
    runtime_blind_spot = subset_summary(
        runtime_current["cases"],
        lambda row: bool(row["blind_spot_expected_codes"]),
    )

    report = {
        "schema_version": "contract-review-independent-eval-v1",
        "status": "completed",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(cases),
        "dataset": str(args.cases),
        "evaluation_scope": (
            "Independent expected-article retrieval benchmark for Contract Review. "
            "This is not a legal-advice accuracy certification."
        ),
        "best_fixed_k_by_macro_f1": best_fixed["fixed_k"],
        "best_fixed_summary": best_fixed["summary"],
        "runtime_current_summary": runtime_current["summary"],
        "runtime_current_stratified": {
            "expected_within_current_preferred_sets": runtime_in_preferred,
            "expected_outside_current_preferred_sets": runtime_blind_spot,
        },
        "blind_spot_cases": blind_spot_cases,
        "variants": variants,
    }

    out_json = args.output_dir / "contract_review_independent_eval.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Contract Review Independent Evidence Benchmark",
        "",
        f"Cases: {len(cases)}. Expected article codes are declared per case and are not generated from preferred_article_codes.",
        "",
        "| Variant | Macro P | Macro R | Macro F1 | Micro P | Micro R | Micro F1 | Exact-set | MRR | Outside-exp. | Sources/case |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in variants:
        s = item["summary"]
        lines.append(
            f"| {item['variant']} | {s['precision']:.4f} | {s['recall']:.4f} | {s['f1']:.4f} | "
            f"{s['micro_precision']:.4f} | {s['micro_recall']:.4f} | {s['micro_f1']:.4f} | "
            f"{s['exact_set_match']:.4f} | {s['mrr']:.4f} | {s['noise_rate']:.4f} | {s['sources_per_case']:.2f} |"
        )
    lines += [
        "",
        f"- Best fixed K by macro F1: **{best_fixed['fixed_k']}**.",
        f"- Runtime-current macro F1: **{runtime_current['summary']['f1']:.4f}**.",
        f"- Independent blind-spot cases outside current preferred sets: **{len(blind_spot_cases)}**.",
        f"- Runtime-current recall on cases fully covered by current preferred sets: **{runtime_in_preferred.get('recall', 0.0):.4f}**.",
        f"- Runtime-current recall on blind-spot cases: **{runtime_blind_spot.get('recall', 0.0):.4f}**.",
        "",
        "## Blind-spot cases",
        "",
    ]
    for item in blind_spot_cases:
        lines.append(
            f"- `{item['id']}` ({item['category']}): expected outside preferred = "
            f"{', '.join(item['expected_outside_preferred'])}; recall={item['recall']:.4f}."
        )
    lines += [
        "",
        "Interpretation: this benchmark evaluates evidence-source selection against independently declared expected articles. "
        "It intentionally includes several expected articles outside the current category preferred lists to expose coverage blind spots. "
        "It should be reported as an engineering retrieval evaluation, not as independent expert legal validation.",
    ]
    (args.output_dir / "benchmark_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "best_fixed_k_by_macro_f1": best_fixed["fixed_k"],
        "best_fixed_summary": best_fixed["summary"],
        "runtime_current_summary": runtime_current["summary"],
        "runtime_current_stratified": {
            "within_preferred": runtime_in_preferred,
            "blind_spot": runtime_blind_spot,
        },
        "blind_spot_case_count": len(blind_spot_cases),
        "blind_spot_cases": blind_spot_cases,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
