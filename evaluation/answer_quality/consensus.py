"""Conservative exact-consensus reporting for independent annotations."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from evaluation.answer_quality.annotations import AnnotationPayload, AnnotationRecord


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


def _claim_fingerprint(annotation: AnnotationPayload) -> dict[str, Any]:
    required = sorted(
        (
            _normalize_text(claim.text),
            tuple(sorted(claim.supported_by_article_codes)),
            tuple(sorted(claim.supported_by_chunk_ids)),
        )
        for claim in annotation.required_claims
    )
    forbidden = sorted(
        (
            _normalize_text(claim.text),
            _normalize_text(claim.reason),
        )
        for claim in annotation.forbidden_claims
    )
    return {
        "expected_status": annotation.expected_status.value,
        "required_claims": required,
        "forbidden_claims": forbidden,
    }


def build_consensus_report(
    records: list[AnnotationRecord],
    expected_models: list[str],
) -> dict[str, Any]:
    if len(expected_models) < 3 or len(set(expected_models)) != len(
        expected_models
    ):
        raise ValueError("consensus requires at least three unique models")
    grouped: dict[str, list[AnnotationRecord]] = defaultdict(list)
    for record in records:
        grouped[record.case_id].append(record)

    cases: list[dict[str, Any]] = []
    for case_id in sorted(grouped):
        case_records = grouped[case_id]
        by_model = {record.requested_model: record for record in case_records}
        missing_models = [
            model for model in expected_models if model not in by_model
        ]
        errors = {
            model: by_model[model].error
            for model in expected_models
            if model in by_model and by_model[model].status == "error"
        }
        successful = [
            by_model[model]
            for model in expected_models
            if model in by_model and by_model[model].status == "success"
        ]
        fingerprints = [
            _claim_fingerprint(record.parsed_annotation)
            for record in successful
            if record.parsed_annotation is not None
        ]
        exact_consensus = (
            not missing_models
            and not errors
            and len(fingerprints) == len(expected_models)
            and all(value == fingerprints[0] for value in fingerprints[1:])
        )
        cases.append({
            "case_id": case_id,
            "decision": (
                "multi_llm_candidate"
                if exact_consensus
                else "needs_human_adjudication"
            ),
            "exact_consensus": exact_consensus,
            "missing_models": missing_models,
            "model_errors": errors,
            "model_fingerprints": {
                record.requested_model: (
                    _claim_fingerprint(record.parsed_annotation)
                    if record.parsed_annotation is not None
                    else None
                )
                for record in case_records
            },
            "consensus_annotation": (
                successful[0].parsed_annotation.model_dump(mode="json")
                if exact_consensus
                and successful[0].parsed_annotation is not None
                else None
            ),
        })
    return {
        "schema_version": "answer-multi-llm-consensus-v1",
        "status": "REVIEW_REQUIRED",
        "authority_review_status": "pending",
        "expected_models": expected_models,
        "case_count": len(cases),
        "exact_consensus_count": sum(
            item["exact_consensus"] for item in cases
        ),
        "needs_human_adjudication_count": sum(
            not item["exact_consensus"] for item in cases
        ),
        "cases": cases,
    }


def load_records(records_dir: Path) -> list[AnnotationRecord]:
    records: list[AnnotationRecord] = []
    for path in sorted(records_dir.glob("*/*.json")):
        records.append(
            AnnotationRecord.model_validate_json(path.read_text(encoding="utf-8"))
        )
    return records


def write_consensus_report(
    records_dir: Path,
    expected_models: list[str],
    output_path: Path,
) -> dict[str, Any]:
    report = build_consensus_report(
        load_records(records_dir),
        expected_models,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        dir=output_path.parent,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(temporary).replace(output_path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return report
