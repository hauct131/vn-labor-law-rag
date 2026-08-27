#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from app.services.contract_review_service import (
    CATEGORIES,
    _excerpt_for,
    _paragraphs,
    _tokens,
    evidence_retriever,
)


def load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data["cases"])


def raw_scores(retriever, query: str) -> list[tuple[float, int]]:
    query_terms = Counter(_tokens(query))
    total = len(retriever.docs)
    scored = []

    for index, doc in enumerate(retriever.docs):
        score = 0.0
        length = retriever.lengths[index] or 1

        for term, query_count in query_terms.items():
            frequency = doc.get(term, 0)
            if not frequency:
                continue

            df = retriever.document_frequency[term]
            idf = math.log(
                1 + (total - df + 0.5) / (df + 0.5)
            )

            denominator = frequency + 1.2 * (
                1 - 0.75
                + 0.75 * length / retriever.average_length
            )

            score += (
                query_count
                * idf
                * frequency
                * 2.2
                / denominator
            )

        scored.append((score, index))

    return scored


def ranked_articles(
    retriever,
    raw: list[tuple[float, int]],
    preferred: tuple[str, ...],
    bonus: float,
) -> list[dict[str, Any]]:
    scored = []

    for raw_score, index in raw:
        payload = retriever.chunks[index]
        article_code = str(
            payload.get("article_code")
            or payload.get("codification_code")
            or ""
        )

        # Soft flat prior: không ép thứ tự bên trong preferred set.
        ranking_score = raw_score
        if article_code in preferred:
            ranking_score += bonus

        # Match CanonicalEvidenceRetriever.retrieve(): only candidates
        # whose final ranking score is positive are retained.
        if ranking_score <= 0:
            continue

        scored.append(
            (
                ranking_score,
                raw_score,
                str(payload["chunk_id"]),
                article_code,
            )
        )

    scored.sort(key=lambda item: (-item[0], item[2]))

    result = []
    seen = set()

    for ranking_score, raw_score, chunk_id, article_code in scored:
        key = article_code or chunk_id
        if key in seen:
            continue

        seen.add(key)

        result.append(
            {
                "article_code": article_code,
                "raw_score": round(raw_score, 6),
                "ranking_score": round(ranking_score, 6),
            }
        )

    return result


def case_metrics(
    expected: list[str],
    retrieved: list[str],
) -> dict[str, float]:
    exp = set(expected)
    got = set(retrieved)
    matched = exp & got

    precision = len(matched) / len(got) if got else 0.0
    recall = len(matched) / len(exp) if exp else 0.0

    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    mrr = 0.0
    for rank, code in enumerate(retrieved, start=1):
        if code in exp:
            mrr = 1.0 / rank
            break

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "mrr": mrr,
        "any_hit": float(bool(matched)),
        "exact_set_match": float(got == exp),
        "noise_rate": 1.0 - precision,
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    names = (
        "precision",
        "recall",
        "f1",
        "mrr",
        "any_hit",
        "exact_set_match",
        "noise_rate",
    )

    return {
        name: round(
            mean(row["metrics"][name] for row in rows),
            6,
        )
        for name in names
    }


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--cases",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--bonuses",
        type=float,
        nargs="+",
        default=(0, 0.5, 1, 2, 4, 8, 16, 32),
    )

    parser.add_argument(
        "--max-k",
        type=int,
        default=8,
    )

    parser.add_argument(
        "--min-macro-recall",
        type=float,
        default=0.83,
    )

    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    cases = load_cases(args.cases)
    retriever = evidence_retriever()
    rules = {rule.key: rule for rule in CATEGORIES}

    prepared = []

    for case in cases:
        rule = rules[case["category"]]

        excerpt = _excerpt_for(
            rule,
            _paragraphs(case["text"]),
        )

        query = rule.query
        if excerpt:
            query += f" Nội dung hợp đồng: {excerpt[:500]}"

        expected = list(case["expected_article_codes"])

        outside = sorted(
            set(expected) - set(rule.preferred_article_codes)
        )

        prepared.append(
            {
                "case": case,
                "rule": rule,
                "excerpt": excerpt,
                "raw": raw_scores(retriever, query),
                "outside": outside,
            }
        )

    trials = []

    for bonus in args.bonuses:
        fully_ranked = {}

        for item in prepared:
            case = item["case"]
            rule = item["rule"]

            fully_ranked[case["id"]] = ranked_articles(
                retriever,
                item["raw"],
                rule.preferred_article_codes,
                bonus,
            )

        for k in range(1, args.max_k + 1):
            rows = []

            for item in prepared:
                case = item["case"]

                ranked = fully_ranked[case["id"]]
                retrieved = [
                    row["article_code"]
                    for row in ranked[:k]
                ]

                rows.append(
                    {
                        "id": case["id"],
                        "category": case["category"],
                        "expected_article_codes": (
                            case["expected_article_codes"]
                        ),
                        "retrieved_article_codes": retrieved,
                        "outside_preferred": item["outside"],
                        "excerpt_detected": bool(item["excerpt"]),
                        "metrics": case_metrics(
                            case["expected_article_codes"],
                            retrieved,
                        ),
                    }
                )

            summary = aggregate(rows)

            within = [
                row for row in rows
                if not row["outside_preferred"]
            ]

            blind = [
                row for row in rows
                if row["outside_preferred"]
            ]

            trial = {
                "bonus": bonus,
                "k": k,
                "summary": summary,
                "within_preferred": aggregate(within),
                "blind_spot": aggregate(blind),
                "detection_rate": round(
                    mean(
                        float(row["excerpt_detected"])
                        for row in rows
                    ),
                    6,
                ),
                "cases": rows,
            }

            trials.append(trial)

    # Production selection is conservative:
    # 1. preserve full recall on cases already covered by the current
    #    preferred sets;
    # 2. require a minimum overall macro recall;
    # 3. among those trials, maximize macro F1.
    safe_trials = [
        trial
        for trial in trials
        if trial["within_preferred"]["recall"] >= 1.0 - 1e-12
    ]

    eligible = [
        trial
        for trial in safe_trials
        if trial["summary"]["recall"] >= args.min_macro_recall
    ]

    selection_pool = eligible or safe_trials or trials

    selected = max(
        selection_pool,
        key=lambda trial: (
            trial["summary"]["f1"],
            trial["summary"]["recall"],
            trial["blind_spot"]["recall"],
            trial["summary"]["mrr"],
            -trial["summary"]["noise_rate"],
            -trial["k"],
            -trial["bonus"],
        ),
    )

    trials.sort(
        key=lambda trial: (
            trial["summary"]["f1"],
            trial["summary"]["recall"],
            trial["blind_spot"]["recall"],
        ),
        reverse=True,
    )

    report = {
        "schema_version": (
            "contract-review-preferred-bonus-sensitivity-v1"
        ),
        "case_count": len(cases),
        "selection_rule": {
            "required_within_preferred_recall": 1.0,
            "minimum_macro_recall": args.min_macro_recall,
            "objective": (
                "Preserve within-preferred recall at 1.0 and meet "
                "minimum overall macro recall; then maximize macro F1. "
                "Tie-break by overall recall, blind-spot recall, MRR, "
                "lower noise, lower K, and lower bonus."
            ),
        },
        "selected": selected,
        "trials": trials,
    }

    json_path = (
        args.output_dir
        / "preferred_bonus_sensitivity.json"
    )

    json_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Contract Review Preferred Bonus Sensitivity",
        "",
        (
            "Soft prior experiment. Preferred articles receive a "
            "flat bounded bonus; the legacy +1000 priority boost "
            "is not used in these trials."
        ),
        "",
        (
            "Selection constraints: within-preferred recall = 1.00 "
            f"and overall macro recall >= {args.min_macro_recall:.2f}."
        ),
        "",
        "| Bonus | K | Macro P | Macro R | Macro F1 | MRR | "
        "Blind R | Within R | Outside-exp. |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for trial in trials:
        s = trial["summary"]

        lines.append(
            f"| {trial['bonus']:g} "
            f"| {trial['k']} "
            f"| {s['precision']:.4f} "
            f"| {s['recall']:.4f} "
            f"| {s['f1']:.4f} "
            f"| {s['mrr']:.4f} "
            f"| {trial['blind_spot']['recall']:.4f} "
            f"| {trial['within_preferred']['recall']:.4f} "
            f"| {s['noise_rate']:.4f} |"
        )

    lines += [
        "",
        "## Selected",
        "",
        f"- Bonus: **{selected['bonus']:g}**",
        f"- K: **{selected['k']}**",
        (
            "- Macro Precision / Recall / F1: "
            f"**{selected['summary']['precision']:.4f} / "
            f"{selected['summary']['recall']:.4f} / "
            f"{selected['summary']['f1']:.4f}**"
        ),
        (
            "- Blind-spot Recall: "
            f"**{selected['blind_spot']['recall']:.4f}**"
        ),
        (
            "- Within-preferred Recall: "
            f"**{selected['within_preferred']['recall']:.4f}**"
        ),
        (
            "- Detection rate: "
            f"**{selected['detection_rate']:.4f}**"
        ),
    ]

    (
        args.output_dir / "benchmark_summary.md"
    ).write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "selected_bonus": selected["bonus"],
                "selected_k": selected["k"],
                "summary": selected["summary"],
                "blind_spot": selected["blind_spot"],
                "within_preferred": (
                    selected["within_preferred"]
                ),
                "detection_rate": (
                    selected["detection_rate"]
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
