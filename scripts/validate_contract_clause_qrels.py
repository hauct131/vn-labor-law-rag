#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


CATEGORIES = (
    "probation",
    "salary",
    "working_time",
    "termination",
)

VALID_RELEVANCE = {0, 1, 2, 3}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(
            lambda: file.read(1024 * 1024),
            b"",
        ):
            digest.update(chunk)

    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate contract clause retrieval qrels "
            "against exported contract sections."
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
        "--guideline",
        type=Path,
        default=None,
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    sections_path = args.sections.resolve()
    qrels_path = args.qrels.resolve()

    if not sections_path.is_file():
        raise FileNotFoundError(
            f"Sections file not found: {sections_path}"
        )

    if not qrels_path.is_file():
        raise FileNotFoundError(
            f"Qrels file not found: {qrels_path}"
        )

    sections_data = json.loads(
        sections_path.read_text(encoding="utf-8")
    )

    qrels_data = json.loads(
        qrels_path.read_text(encoding="utf-8")
    )

    documents = sections_data.get("documents")

    if not isinstance(documents, list):
        raise ValueError(
            "Sections JSON must contain top-level 'documents' list"
        )

    queries = qrels_data.get("queries")

    if not isinstance(queries, list):
        raise ValueError(
            "Qrels JSON must contain top-level 'queries' list"
        )

    docs_by_id = {
        document["document_id"]: document
        for document in documents
    }

    errors: list[str] = []

    expected_query_ids = {
        f"{document_id}:{category}"
        for document_id in docs_by_id
        for category in CATEGORIES
    }

    actual_query_ids: list[str] = []

    label_counts = {
        0: 0,
        1: 0,
        2: 0,
        3: 0,
    }

    no_relevant_queries = []

    for query in queries:
        query_id = query.get("query_id")
        document_id = query.get("document_id")
        category = query.get("category")

        if not isinstance(query_id, str):
            errors.append(
                "Query without valid query_id"
            )
            continue

        actual_query_ids.append(query_id)

        if document_id not in docs_by_id:
            errors.append(
                f"{query_id}: unknown document_id {document_id!r}"
            )
            continue

        if category not in CATEGORIES:
            errors.append(
                f"{query_id}: invalid category {category!r}"
            )
            continue

        expected_query_id = (
            f"{document_id}:{category}"
        )

        if query_id != expected_query_id:
            errors.append(
                f"{query_id}: expected query_id "
                f"{expected_query_id!r}"
            )

        source_sections = docs_by_id[
            document_id
        ]["sections"]

        source_section_ids = {
            section["section_id"]
            for section in source_sections
        }

        judgments = query.get("judgments")

        if not isinstance(judgments, list):
            errors.append(
                f"{query_id}: judgments must be a list"
            )
            continue

        judgment_ids = [
            judgment.get("section_id")
            for judgment in judgments
        ]

        if len(judgment_ids) != len(
            set(judgment_ids)
        ):
            errors.append(
                f"{query_id}: duplicate section judgments"
            )

        if set(judgment_ids) != source_section_ids:
            errors.append(
                f"{query_id}: section coverage mismatch"
            )

        has_relevant = False
        has_primary = False

        for judgment in judgments:
            section_id = judgment.get("section_id")
            relevance = judgment.get("relevance")

            if relevance not in VALID_RELEVANCE:
                errors.append(
                    f"{query_id} / {section_id}: "
                    f"invalid relevance {relevance!r}"
                )
                continue

            label_counts[relevance] += 1

            if relevance > 0:
                has_relevant = True

                if not judgment.get(
                    "reason",
                    "",
                ).strip():
                    errors.append(
                        f"{query_id} / {section_id}: "
                        "positive judgment missing reason"
                    )

            if relevance == 3:
                has_primary = True

        if query.get("has_relevant") != has_relevant:
            errors.append(
                f"{query_id}: has_relevant flag mismatch"
            )

        if query.get("has_primary") != has_primary:
            errors.append(
                f"{query_id}: has_primary flag mismatch"
            )

        if not has_relevant:
            no_relevant_queries.append(
                query_id
            )

    if len(actual_query_ids) != len(
        set(actual_query_ids)
    ):
        errors.append(
            "Duplicate query IDs"
        )

    if set(actual_query_ids) != expected_query_ids:
        missing = sorted(
            expected_query_ids
            - set(actual_query_ids)
        )

        extra = sorted(
            set(actual_query_ids)
            - expected_query_ids
        )

        if missing:
            errors.append(
                "Missing queries: "
                + ", ".join(missing)
            )

        if extra:
            errors.append(
                "Unexpected queries: "
                + ", ".join(extra)
            )

    frozen_inputs = qrels_data.get(
        "frozen_inputs",
        {},
    )

    expected_sections_sha = frozen_inputs.get(
        "sections_sha256"
    )

    if expected_sections_sha:
        actual_sections_sha = sha256_file(
            sections_path
        )

        if expected_sections_sha != actual_sections_sha:
            errors.append(
                "Sections SHA-256 mismatch"
            )

    if args.guideline is not None:
        guideline_path = (
            args.guideline.resolve()
        )

        expected_guideline_sha = frozen_inputs.get(
            "guideline_sha256"
        )

        if expected_guideline_sha:
            actual_guideline_sha = sha256_file(
                guideline_path
            )

            if (
                expected_guideline_sha
                != actual_guideline_sha
            ):
                errors.append(
                    "Guideline SHA-256 mismatch"
                )

    print("Contract clause qrels validation")
    print("================================")
    print(f"Documents: {len(documents)}")
    print(f"Queries: {len(queries)}")
    print(
        "Label counts: "
        f"{label_counts}"
    )
    print(
        "No-relevant queries: "
        f"{len(no_relevant_queries)}"
    )

    for query_id in no_relevant_queries:
        print(f"  - {query_id}")

    if errors:
        print()
        print("FAILED")
        print("------")

        for error in errors:
            print(f"[ERROR] {error}")

        raise SystemExit(1)

    print()
    print("PASS")
    print("----")
    print(
        "Structure, coverage, labels, evidence reasons, "
        "flags, and frozen hashes are consistent."
    )


if __name__ == "__main__":
    main()