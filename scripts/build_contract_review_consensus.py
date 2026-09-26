#!/usr/bin/env python3
"""Build a transparent 3-of-4 consensus audit for Contract Review sources.

Only Python's standard library is used. The script preserves the original
audit rows, exports the resolved subset, exports unresolved rows, and writes a
manifest plus a short Markdown report. It deliberately does not call the
existing top-k scorer because filtering individual rows breaks the fixed top-4
shape for cases containing an unresolved source.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


VALID_LABELS = {0, 1, 2}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit", required=True, help="Full exported source relevance audit CSV")
    parser.add_argument(
        "--judge",
        action="append",
        required=True,
        help="JSON label file; repeat exactly four times",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--min-votes", type=int, default=3)
    return parser.parse_args()


def load_judge(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON list")
    result: dict[str, dict] = {}
    for item in data:
        audit_id = str(item.get("audit_id", "")).strip()
        if not audit_id:
            raise ValueError(f"{path}: missing audit_id")
        if audit_id in result:
            raise ValueError(f"{path}: duplicate audit_id={audit_id}")
        try:
            label = int(item["manual_label"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path}: invalid label for {audit_id}") from exc
        if label not in VALID_LABELS:
            raise ValueError(f"{path}: label must be 0, 1, or 2 for {audit_id}")
        result[audit_id] = {
            "label": label,
            "reason": str(item.get("manual_reason", "")).strip(),
        }
    return result


def fleiss_kappa(vote_rows: list[list[int]]) -> float:
    n_items = len(vote_rows)
    n_raters = len(vote_rows[0])
    agreement = []
    totals = Counter()
    for votes in vote_rows:
        counts = Counter(votes)
        totals.update(votes)
        agreement.append(
            sum(counts[label] * (counts[label] - 1) for label in VALID_LABELS)
            / (n_raters * (n_raters - 1))
        )
    observed = sum(agreement) / n_items
    proportions = {
        label: totals[label] / (n_items * n_raters) for label in VALID_LABELS
    }
    expected = sum(value * value for value in proportions.values())
    return (observed - expected) / (1 - expected)


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    if len(args.judge) != 4:
        raise SystemExit("ERROR: pass --judge exactly four times")
    if args.min_votes < 3 or args.min_votes > 4:
        raise SystemExit("ERROR: --min-votes must be 3 or 4")

    audit_path = Path(args.audit)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with audit_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        base_fields = list(reader.fieldnames or [])
        audit_rows = list(reader)
    if "audit_id" not in base_fields:
        raise SystemExit("ERROR: audit CSV has no audit_id column")
    audit_ids = [row["audit_id"].strip() for row in audit_rows]
    if not audit_ids or len(audit_ids) != len(set(audit_ids)):
        raise SystemExit("ERROR: audit_id values are empty or duplicated")

    judges = [load_judge(Path(path)) for path in args.judge]
    expected_ids = set(audit_ids)
    for index, judge in enumerate(judges, start=1):
        missing = sorted(expected_ids - set(judge))
        extra = sorted(set(judge) - expected_ids)
        if missing or extra:
            raise SystemExit(
                f"ERROR: judge {index} ID mismatch; missing={missing}, extra={extra}"
            )

    all_rows: list[dict] = []
    resolved_rows: list[dict] = []
    unresolved_rows: list[dict] = []
    vote_rows: list[list[int]] = []
    label_counts = Counter()
    category_counts: dict[str, Counter] = defaultdict(Counter)
    case_status: dict[str, bool] = defaultdict(lambda: True)

    for source_row in audit_rows:
        audit_id = source_row["audit_id"].strip()
        votes = [judge[audit_id]["label"] for judge in judges]
        vote_rows.append(votes)
        counts = Counter(votes)
        consensus_label, consensus_votes = sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )[0]
        resolved = consensus_votes >= args.min_votes
        case_id = source_row.get("case_id", "").strip() or audit_id.split("__", 1)[0]
        if not resolved:
            case_status[case_id] = False
        else:
            case_status.setdefault(case_id, True)

        enriched = dict(source_row)
        for index, judge in enumerate(judges, start=1):
            enriched[f"judge_{index}_label"] = judge[audit_id]["label"]
            enriched[f"judge_{index}_reason"] = judge[audit_id]["reason"]
        enriched["consensus_label"] = consensus_label if resolved else ""
        enriched["consensus_votes"] = consensus_votes
        enriched["consensus_status"] = "resolved" if resolved else "unresolved"
        enriched["vote_pattern"] = ",".join(str(vote) for vote in votes)
        all_rows.append(enriched)

        if resolved:
            scored = dict(source_row)
            scored["manual_label"] = consensus_label
            scored["manual_reason"] = (
                f"Đồng thuận {consensus_votes}/4 theo rubric 0/1/2; "
                f"vote_pattern={enriched['vote_pattern']}."
            )
            scored["consensus_votes"] = consensus_votes
            resolved_rows.append(scored)
            label_counts[consensus_label] += 1
            category_counts[source_row.get("category", "unknown")][consensus_label] += 1
        else:
            unresolved_rows.append(enriched)

    total = len(audit_rows)
    resolved = len(resolved_rows)
    unresolved = len(unresolved_rows)
    relevant = label_counts[1] + label_counts[2]
    complete_cases = sum(case_status.values())
    total_cases = len(case_status)

    manifest = {
        "schema_version": "contract-review-consensus-v1",
        "method": "independent 4-judge semantic relevance labeling",
        "minimum_votes": args.min_votes,
        "input_pair_count": total,
        "resolved_pair_count": resolved,
        "unresolved_pair_count": unresolved,
        "consensus_coverage": round(resolved / total, 6),
        "label_counts_resolved": {
            "irrelevant_0": label_counts[0],
            "supporting_1": label_counts[1],
            "direct_2": label_counts[2],
        },
        "relevant_pair_count_resolved": relevant,
        "conditional_relevance_rate": round(relevant / resolved, 6),
        "full_set_relevance_lower_bound": round(relevant / total, 6),
        "full_set_relevance_upper_bound": round((relevant + unresolved) / total, 6),
        "fleiss_kappa": round(fleiss_kappa(vote_rows), 6),
        "case_count": total_cases,
        "complete_consensus_case_count": complete_cases,
        "partial_consensus_case_count": total_cases - complete_cases,
        "rank_aware_top_k_metrics_valid_on_85_rows": False,
        "category_label_counts": {
            category: {
                "irrelevant_0": counts[0],
                "supporting_1": counts[1],
                "direct_2": counts[2],
            }
            for category, counts in sorted(category_counts.items())
        },
        "limitations": [
            "Labels represent multi-model semantic consensus, not expert legal adjudication.",
            "The resolved subset is selected by agreement and is not a random sample.",
            "Removing individual rows breaks the fixed top-4 shape for partial cases.",
        ],
    }

    judge_fields = []
    for index in range(1, 5):
        judge_fields.extend([f"judge_{index}_label", f"judge_{index}_reason"])
    all_fields = base_fields + judge_fields + [
        "consensus_label",
        "consensus_votes",
        "consensus_status",
        "vote_pattern",
    ]
    resolved_fields = base_fields + ["consensus_votes"]
    write_csv(output_dir / "source_relevance_consensus_all_96.csv", all_fields, all_rows)
    write_csv(output_dir / "source_relevance_consensus_85.csv", resolved_fields, resolved_rows)
    write_csv(output_dir / "source_relevance_unresolved_11.csv", all_fields, unresolved_rows)

    with (output_dir / "consensus_manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    report = f"""# Contract Review semantic consensus audit

- Initial source-case pairs: {total}
- Consensus threshold: {args.min_votes}/4
- Resolved pairs: {resolved} ({resolved / total:.2%})
- Unresolved pairs: {unresolved} ({unresolved / total:.2%})
- Direct (2): {label_counts[2]}
- Supporting (1): {label_counts[1]}
- Irrelevant (0): {label_counts[0]}
- Relevant among resolved (1 or 2): {relevant}/{resolved} ({relevant / resolved:.2%})
- Full-set binary relevance bounds: {relevant / total:.2%}–{(relevant + unresolved) / total:.2%}
- Fleiss' kappa: {manifest['fleiss_kappa']:.3f}
- Cases retaining all four resolved sources: {complete_cases}/{total_cases}

The 85-row file is a consensus-filtered subset. It must not be reported as a
complete Precision@4 evaluation because cases containing an unresolved source
no longer have four labelled sources. These labels indicate semantic relevance
agreement and are not expert legal adjudication.
"""
    (output_dir / "consensus_report.md").write_text(report, encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
