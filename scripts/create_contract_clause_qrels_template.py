#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SOURCE = (
    ROOT
    / "data"
    / "evaluation"
    / "contract_review_clause_retrieval"
    / "dev"
    / "sections_for_annotation.json"
)

CATEGORIES = (
    "probation",
    "salary",
    "working_time",
    "termination",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a blank clause-retrieval qrels template "
            "from exported contract sections."
        )
    )

    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=(
            "Path to sections_for_annotation.json. "
            f"Default: {DEFAULT_SOURCE}"
        ),
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output qrels draft path. "
            "Default: qrels_draft.json next to --source."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    source = args.source.resolve()

    output = (
        args.output.resolve()
        if args.output is not None
        else source.parent / "qrels_draft.json"
    )

    if not source.is_file():
        raise FileNotFoundError(
            f"Source annotation file not found: {source}"
        )

    if output == source:
        raise ValueError(
            "--output must not overwrite --source"
        )

    data = json.loads(
        source.read_text(encoding="utf-8")
    )

    if "documents" not in data:
        raise ValueError(
            "Source JSON does not contain 'documents'"
        )

    judgments = []

    for document in data["documents"]:
        for category in CATEGORIES:
            section_judgments = []

            for section in document["sections"]:
                section_judgments.append(
                    {
                        "section_id":
                            section["section_id"],
                        "section_index":
                            section["section_index"],
                        "heading":
                            section["heading"],
                        "relevance":
                            None,
                        "reason":
                            "",
                    }
                )

            judgments.append(
                {
                    "document_id":
                        document["document_id"],
                    "file":
                        document["file"],
                    "category":
                        category,
                    "judgments":
                        section_judgments,
                }
            )

    result = {
        "schema_version":
            "contract-clause-qrels-v1",
        "status":
            "draft_unreviewed",
        "relevance_scale": {
            "0": "not_relevant",
            "1": "related_mention",
            "2": "relevant_secondary",
            "3": "primary",
        },
        "judgments":
            judgments,
    }

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output.write_text(
        json.dumps(
            result,
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

    print(f"Source: {source}")
    print(f"Documents: {len(data['documents'])}")
    print(f"Queries: {len(judgments)}")
    print(f"Section judgments: {total}")
    print(f"Wrote: {output}")


if __name__ == "__main__":
    main()