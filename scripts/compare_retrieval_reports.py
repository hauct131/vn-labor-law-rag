#!/usr/bin/env python3
"""Print a compact comparison of retrieval benchmark JSON reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


METRICS = [
    "any_article_hit",
    "all_article_hit",
    "article_recall",
    "article_mrr",
    "any_evidence_hit",
    "all_evidence_hit",
    "evidence_recall",
    "evidence_mrr",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--k", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    level = f"at_{args.k}"

    rows = []
    for path in args.reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        metrics = report["summary"][level]
        name = report["retriever"]["name"]
        rows.append((name, path, metrics))

    header = ["retriever", *METRICS]
    widths = {
        column: len(column)
        for column in header
    }

    rendered = []
    for name, path, metrics in rows:
        row = {
            "retriever": name,
            **{
                metric: f"{metrics[metric]:.4f}"
                for metric in METRICS
            },
        }
        rendered.append(row)
        for column, value in row.items():
            widths[column] = max(widths[column], len(value))

    print(
        "  ".join(
            column.ljust(widths[column])
            for column in header
        )
    )
    print(
        "  ".join("-" * widths[column] for column in header)
    )
    for row in rendered:
        print(
            "  ".join(
                row[column].ljust(widths[column])
                for column in header
            )
        )


if __name__ == "__main__":
    main()
