#!/usr/bin/env python3

import hashlib
import json
import re
from pathlib import Path
from typing import Iterator

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.services.contract_review.segmenter import segment_contract


ROOT = Path(__file__).resolve().parents[1]

DATASET_DIR = (
    ROOT
    / "data"
    / "evaluation"
    / "contract_review_clause_retrieval"
)

DEV_DIR = DATASET_DIR / "dev"
CONTRACTS_DIR = DEV_DIR / "contracts"

OUTPUT_JSON = DEV_DIR / "sections_for_annotation.json"
OUTPUT_MD = DEV_DIR / "sections_for_annotation.md"


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def iter_docx_blocks(path: Path) -> Iterator[dict[str, str]]:
    """
    Extract paragraphs and table rows in document order.

    Each returned item contains:
        kind: paragraph | table_row
        text: normalized textual content

    Table rows are represented as:
        cell 1 | cell 2 | cell 3

    We retain `kind` in the exported audit file so extraction can
    be checked manually, while the current segmenter still receives
    only textual blocks.
    """
    document = Document(path)

    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph = Paragraph(child, document)
            text = normalize_whitespace(paragraph.text)

            if text:
                yield {
                    "kind": "paragraph",
                    "text": text,
                }

        elif child.tag.endswith("}tbl"):
            table = Table(child, document)

            for row in table.rows:
                cells = [
                    normalize_whitespace(cell.text)
                    for cell in row.cells
                ]

                # Preserve cell positions, including an empty cell between
                # two non-empty cells.
                if not any(cells):
                    continue

                yield {
                    "kind": "table_row",
                    "text": " | ".join(cells),
                }


def build_document_record(path: Path) -> dict:
    extracted_blocks = list(iter_docx_blocks(path))

    segment_input = [
        block["text"]
        for block in extracted_blocks
    ]

    sections = segment_contract(segment_input)

    serialized_sections = []

    for section in sections:
        serialized_sections.append(
            {
                "section_index": section.index,
                "section_id": (
                    f"{path.stem}:section:{section.index}"
                ),
                "heading": section.heading,
                "text": section.text,
            }
        )

    return {
        "document_id": path.stem,
        "file": path.name,
        "sha256": sha256_file(path),
        "block_count": len(extracted_blocks),
        "section_count": len(sections),
        "source_blocks": [
            {
                "block_index": index,
                **block,
            }
            for index, block in enumerate(extracted_blocks)
        ],
        "sections": serialized_sections,
    }


def write_markdown(documents: list[dict]) -> None:
    lines = [
        "# Contract sections for manual annotation",
        "",
        "> AUTO-GENERATED FILE. Do not use as gold until manually reviewed.",
        "",
    ]

    for document in documents:
        lines.extend(
            [
                "---",
                "",
                f"# Document: {document['document_id']}",
                "",
                f"- File: `{document['file']}`",
                f"- SHA256: `{document['sha256']}`",
                f"- Extracted blocks: {document['block_count']}",
                f"- Sections: {document['section_count']}",
                "",
            ]
        )

        for section in document["sections"]:
            heading = section["heading"] or "[PREAMBLE]"

            lines.extend(
                [
                    (
                        f"## Section {section['section_index']}: "
                        f"{heading}"
                    ),
                    "",
                    "```text",
                    section["text"],
                    "```",
                    "",
                    "Annotation checklist:",
                    "",
                    "- [ ] Extraction checked against DOCX",
                    "- [ ] Section boundary correct",
                    "- probation: ___",
                    "- salary: ___",
                    "- working_time: ___",
                    "- termination: ___",
                    "- Notes:",
                    "",
                ]
            )

    OUTPUT_MD.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    paths = sorted(
        CONTRACTS_DIR.glob("*.docx"),
        key=lambda path: path.name.casefold(),
    )

    if not paths:
        raise RuntimeError(
            f"No DOCX contracts found in {CONTRACTS_DIR}"
        )

    documents = []

    for path in paths:
        record = build_document_record(path)
        documents.append(record)

        print(
            f"[OK] {path.name}: "
            f"{record['block_count']} blocks -> "
            f"{record['section_count']} sections"
        )

    output = {
        "schema_version":
            "contract-clause-annotation-source-v1",
        "status":
            "unreviewed",
        "documents":
            documents,
    }

    OUTPUT_JSON.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    write_markdown(documents)

    print()
    print(f"Documents: {len(documents)}")
    print(f"JSON: {OUTPUT_JSON}")
    print(f"Review: {OUTPUT_MD}")


if __name__ == "__main__":
    main()
