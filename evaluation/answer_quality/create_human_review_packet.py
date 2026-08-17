#!/usr/bin/env python3
"""CLI and library to generate independent human review packets.

Creates a structured JSON packet, a human-readable Markdown guide, and a manifest
containing dataset, adjudication, and canonical corpus hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.answer_quality.adjudication import ManualAdjudicationArtifact
from evaluation.answer_quality.apply_adjudication import (
    DEFAULT_ADJUDICATION,
    _load_chunk_index,
    _sha256,
)
from evaluation.answer_quality.schema import (
    AuthorityReviewStatus,
    load_answer_quality_dataset,
)


DEFAULT_DATASET = Path(
    "data/evaluation/answer-quality-v1/multi_llm_reviewed_questions.json"
)


class PacketCreationError(ValueError):
    """Raised when a human review packet cannot be constructed."""


def build_human_review_packet(
    dataset_path: Path,
    *,
    adjudication_path: Path | None = None,
    repo_root: Path,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Build structured JSON, Markdown packet, and manifest dict."""

    dataset = load_answer_quality_dataset(dataset_path)

    corpus_path = repo_root / dataset.source_corpus_path
    if not corpus_path.is_file():
        raise PacketCreationError(
            f"declared canonical corpus is missing: {corpus_path}"
        )
    if _sha256(corpus_path) != dataset.source_corpus_sha256:
        raise PacketCreationError("canonical corpus hash mismatch")

    chunk_index = _load_chunk_index(corpus_path)

    adj_sha = ""
    if adjudication_path and adjudication_path.is_file():
        adj_sha = _sha256(adjudication_path)
    elif (repo_root / DEFAULT_ADJUDICATION).is_file():
        adj_sha = _sha256(repo_root / DEFAULT_ADJUDICATION)

    cases_json: list[dict[str, Any]] = []
    md_sections: list[str] = [
        "# Answer Quality Human Review Packet (V1)\n",
        "## Review Instructions\n",
        "1. For each case, inspect the question, evidence chunks, required claims, and forbidden claims.",
        "2. Set `decision` to exactly one of: `accept`, `edit`, or `reject`.",
        "3. Provide `reviewer` (your real name/ID; AI names forbidden) and `reviewed_at` (ISO date/time).",
        "4. If decision is `edit`, supply modified `edited_required_claims` and/or `edited_forbidden_claims` adhering to allowed citations.",
        "5. Leave `authority_review_status` as `pending`.",
        "\n---\n",
    ]

    for question in dataset.questions:
        allowed_chunks: list[dict[str, Any]] = []
        for chunk_id in question.evidence_chunk_ids:
            chunk = chunk_index.get(chunk_id)
            if not chunk:
                raise PacketCreationError(
                    f"case {question.id}: chunk {chunk_id} missing from corpus"
                )
            allowed_chunks.append({
                "chunk_id": chunk.get("chunk_id", ""),
                "article_code": chunk.get("article_code", ""),
                "article_number": chunk.get("article_number", ""),
                "article_title": chunk.get("article_title", ""),
                "content": chunk.get("content", ""),
            })

        case_dict = {
            "case_id": question.id,
            "question": question.question,
            "expected_status": question.expected_status.value,
            "category": question.category,
            "question_type": question.question_type,
            "difficulty": question.difficulty,
            "document_number": question.document_number,
            "document_title": question.document_title,
            "expected_article_codes": question.expected_article_codes,
            "evidence_chunk_ids": question.evidence_chunk_ids,
            "evidence_chunks": allowed_chunks,
            "required_claims": [
                claim.model_dump(mode="json")
                for claim in question.required_claims
            ],
            "forbidden_claims": [
                claim.model_dump(mode="json")
                for claim in question.forbidden_claims
            ],
            "multi_llm_review_notes": question.review_notes,
            "decision": "",  # To be completed by human reviewer
            "reviewer_notes": "",
            "edited_required_claims": [],
            "edited_forbidden_claims": [],
        }
        cases_json.append(case_dict)

        # Markdown representation
        md_buf = [
            f"### Case: {question.id}",
            f"**Question**: {question.question}",
            f"- **Expected Status**: {question.expected_status.value}",
            f"- **Category / Type / Difficulty**: {question.category} / {question.question_type} / {question.difficulty}",
            f"- **Document**: {question.document_number} — {question.document_title}",
            f"- **Expected Articles**: {', '.join(question.expected_article_codes) or 'None'}",
            f"- **Evidence Chunks**: {', '.join(question.evidence_chunk_ids) or 'None'}",
            "\n#### Evidence Chunks (Full Canonical Content):",
        ]
        for chunk in allowed_chunks:
            md_buf.append(
                f"```text\n[{chunk['chunk_id']}] {chunk['article_code']} — {chunk['article_title']}\n"
                f"{chunk['content']}\n```"
            )

        md_buf.append("\n#### Candidate Required Claims:")
        if question.required_claims:
            for claim in question.required_claims:
                md_buf.append(
                    f"- `[{claim.claim_id}]` {claim.text} "
                    f"(Articles: {', '.join(claim.supported_by_article_codes)}, "
                    f"Chunks: {', '.join(claim.supported_by_chunk_ids)})"
                )
        else:
            md_buf.append("- *None*")

        md_buf.append("\n#### Candidate Forbidden Claims:")
        if question.forbidden_claims:
            for claim in question.forbidden_claims:
                md_buf.append(
                    f"- `[{claim.claim_id}]` {claim.text} (Reason: {claim.reason})"
                )
        else:
            md_buf.append("- *None*")

        md_buf.append(
            f"\n**Multi-LLM Notes**: {question.review_notes}\n"
            "**Human Decision**: `[accept / edit / reject]`\n"
            "**Reviewer Notes**: `[Enter notes]`\n"
            "\n---\n"
        )
        md_sections.append("\n".join(md_buf))

    packet_json = {
        "schema_version": "answer-human-adjudication-v1",
        "adjudication_status": "human_adjudicated",
        "authority_review_status": AuthorityReviewStatus.PENDING.value,
        "golden_locked": False,
        "reviewer": "",
        "reviewed_at": "",
        "cases": [
            {
                "case_id": c["case_id"],
                "decision": "",
                "reviewer_notes": "",
                "edited_required_claims": [],
                "edited_forbidden_claims": [],
            }
            for c in cases_json
        ],
        "cases_detail": cases_json,
    }

    manifest = {
        "schema_version": "answer-human-review-manifest-v1",
        "dataset_path": str(dataset_path),
        "dataset_sha256": _sha256(dataset_path),
        "adjudication_sha256": adj_sha,
        "source_corpus_release_id": dataset.source_corpus_release_id,
        "source_corpus_path": dataset.source_corpus_path,
        "source_corpus_sha256": dataset.source_corpus_sha256,
        "case_count": len(dataset.questions),
        "authority_review_status": AuthorityReviewStatus.PENDING.value,
        "golden_locked": False,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    return packet_json, "\n".join(md_sections), manifest


def run(
    dataset_path: Path,
    output_dir: Path,
    *,
    repo_root: Path,
    check: bool,
) -> dict[str, Any]:
    packet_json, markdown_str, manifest = build_human_review_packet(
        dataset_path,
        repo_root=repo_root,
    )

    json_str = json.dumps(packet_json, ensure_ascii=False, indent=2) + "\n"
    manifest_str = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"

    json_path = output_dir / "human-review.json"
    md_path = output_dir / "human-review.md"
    manifest_path = output_dir / "manifest.json"

    if check:
        if (
            not json_path.is_file()
            or not md_path.is_file()
            or not manifest_path.is_file()
        ):
            raise PacketCreationError(f"output dir files missing: {output_dir}")
        if json_path.read_text(encoding="utf-8") != json_str:
            raise PacketCreationError(f"human-review.json is stale: {json_path}")
        status = "check_pass"
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json_str, encoding="utf-8")
        md_path.write_text(markdown_str, encoding="utf-8")
        manifest_path.write_text(manifest_str, encoding="utf-8")
        status = "written"

    return {
        "status": status,
        "case_count": len(packet_json["cases"]),
        "output_dir": str(output_dir),
        "manifest": manifest,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate human review packet for answer quality evaluation."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.path.expanduser("~/Downloads/answer-quality-human-review")),
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = run(
            args.dataset,
            args.output_dir,
            repo_root=args.repo_root,
            check=args.check,
        )
    except (PacketCreationError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
