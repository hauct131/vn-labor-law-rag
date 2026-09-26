#!/usr/bin/env python3

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

DEV_DIR = (
    ROOT
    / "data"
    / "evaluation"
    / "contract_review_clause_retrieval"
    / "dev"
)

SOURCE = DEV_DIR / "sections_for_annotation.json"
OUTPUT = DEV_DIR / "qrels_draft.json"

CATEGORIES = (
    "probation",
    "salary",
    "working_time",
    "termination",
)


def main() -> None:
    data = json.loads(
        SOURCE.read_text(encoding="utf-8")
    )

    judgments = []

    for document in data["documents"]:
        for category in CATEGORIES:
            section_judgments = []

            for section in document["sections"]:
                section_judgments.append(
                    {
                        "section_id": section["section_id"],
                        "section_index": section["section_index"],
                        "heading": section["heading"],
                        "relevance": None,
                        "reason": "",
                    }
                )

            judgments.append(
                {
                    "document_id": document["document_id"],
                    "file": document["file"],
                    "category": category,
                    "judgments": section_judgments,
                }
            )

    output = {
        "schema_version": "contract-clause-qrels-v1",
        "status": "draft_unreviewed",
        "relevance_scale": {
            "0": "not_relevant",
            "1": "related_mention",
            "2": "relevant_secondary",
            "3": "primary",
        },
        "judgments": judgments,
    }

    OUTPUT.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    total = sum(
        len(item["judgments"])
        for item in judgments
    )

    print(f"Documents: {len(data['documents'])}")
    print(f"Queries: {len(judgments)}")
    print(f"Section judgments: {total}")
    print(f"Wrote: {OUTPUT}")


if __name__ == "__main__":
    main()

