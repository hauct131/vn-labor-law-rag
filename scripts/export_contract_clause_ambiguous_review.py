#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Export relevance-1/2 clause judgments for "
            "manual human review."
        )
    )

    parser.add_argument(
        "--sections",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--qrels",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    sections_path = args.sections.resolve()
    qrels_path = args.qrels.resolve()
    output_path = args.output.resolve()

    sections_data = json.loads(
        sections_path.read_text(encoding="utf-8")
    )

    qrels_data = json.loads(
        qrels_path.read_text(encoding="utf-8")
    )

    section_text_by_id = {}

    for document in sections_data["documents"]:
        for section in document["sections"]:
            section_text_by_id[
                section["section_id"]
            ] = section["text"]

    rows = []

    for query in qrels_data["queries"]:
        for judgment in query["judgments"]:
            relevance = judgment["relevance"]

            if relevance not in (1, 2):
                continue

            section_id = judgment["section_id"]

            rows.append(
                {
                    "query_id":
                        query["query_id"],
                    "document_id":
                        query["document_id"],
                    "category":
                        query["category"],
                    "section_id":
                        section_id,
                    "relevance":
                        relevance,
                    "reason":
                        judgment.get("reason", ""),
                    "text":
                        section_text_by_id[section_id],
                }
            )

    rows.sort(
        key=lambda row: (
            row["document_id"],
            row["category"],
            row["section_id"],
        )
    )

    lines = [
        "# Held-out Ambiguous Judgment Review",
        "",
        (
            "> Auto-generated from qrels. "
            "This file does not modify annotations."
        ),
        "",
        f"Cases requiring review: **{len(rows)}**",
        "",
    ]

    for index, row in enumerate(rows, start=1):
        lines.extend(
            [
                "---",
                "",
                (
                    f"## {index}. "
                    f"{row['query_id']}"
                ),
                "",
                f"- Section: `{row['section_id']}`",
                f"- Relevance: **{row['relevance']}**",
                f"- Reason: {row['reason']}",
                "",
                "### Section text",
                "",
                "```text",
                row["text"],
                "```",
                "",
                "### Human review",
                "",
                "- [ ] Label accepted",
                "- [ ] Reason accepted",
                "- [ ] Needs correction",
                "- Notes:",
                "",
            ]
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    print(f"Review cases: {len(rows)}")
    print(f"Wrote: {output_path}")


if __name__ == "__main__":
    main()
