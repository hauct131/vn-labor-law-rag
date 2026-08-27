#!/usr/bin/env python3
"""Run independent OpenRouter annotators and preserve auditable records."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from evaluation.answer_quality.annotations import (
    PROMPT_VERSION,
    AnnotationRecord,
)
from evaluation.answer_quality.consensus import load_records, write_consensus_report
from evaluation.answer_quality.openrouter import (
    OpenRouterAnnotationClient,
    OpenRouterAnnotationError,
    OpenRouterAnnotationResult,
)
from evaluation.answer_quality.prompt import (
    build_messages,
    load_chunk_index,
    prompt_sha256,
)
from evaluation.answer_quality.panel_support import (
    AnnotationReferenceError,
    PanelConfigurationError,
    atomic_json,
    file_sha256,
    record_name,
    resolve_corpus_path,
    validate_annotation_references,
    validate_models,
)
from evaluation.answer_quality.schema import (
    AnswerQualityQuestion,
    load_answer_quality_dataset,
)


def _run_one(
    *,
    run_id: str,
    question: AnswerQualityQuestion,
    model: str,
    messages: list[dict[str, str]],
    client_options: dict[str, Any],
) -> AnnotationRecord:
    started_at = datetime.now(UTC)
    started_clock = perf_counter()
    prompt_hash = prompt_sha256(messages)
    result: OpenRouterAnnotationResult | None = None
    try:
        client = OpenRouterAnnotationClient(**client_options)
        result = client.annotate(model=model, messages=messages)
        validate_annotation_references(
            question,
            result.parsed_annotation,
        )
    except OpenRouterAnnotationError as exc:
        finished_at = datetime.now(UTC)
        return AnnotationRecord(
            run_id=run_id,
            case_id=question.id,
            requested_model=model,
            prompt_sha256=prompt_hash,
            request_sha256=exc.request_sha256 or "0" * 64,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=(perf_counter() - started_clock) * 1000,
            status="error",
            raw_content=exc.raw_content,
            provider_response=exc.provider_response,
            error=str(exc),
        )
    except AnnotationReferenceError as exc:
        if result is None:
            raise
        finished_at = datetime.now(UTC)
        return AnnotationRecord(
            run_id=run_id,
            case_id=question.id,
            requested_model=model,
            resolved_model=result.resolved_model,
            response_id=result.response_id,
            prompt_sha256=prompt_hash,
            request_sha256=result.request_sha256,
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=(perf_counter() - started_clock) * 1000,
            status="error",
            raw_content=result.raw_content,
            usage=result.usage,
            provider_response=result.provider_response,
            error=str(exc),
        )

    finished_at = datetime.now(UTC)
    return AnnotationRecord(
        run_id=run_id,
        case_id=question.id,
        requested_model=model,
        resolved_model=result.resolved_model,
        response_id=result.response_id,
        prompt_sha256=prompt_hash,
        request_sha256=result.request_sha256,
        started_at=started_at,
        finished_at=finished_at,
        latency_ms=(perf_counter() - started_clock) * 1000,
        status="success",
        raw_content=result.raw_content,
        parsed_annotation=result.parsed_annotation,
        usage=result.usage,
        provider_response=result.provider_response,
    )


def _existing_record(path: Path) -> AnnotationRecord | None:
    if not path.is_file():
        return None
    return AnnotationRecord.model_validate_json(path.read_text(encoding="utf-8"))


def run_panel(
    *,
    dataset_path: Path,
    output_dir: Path,
    models: list[str],
    repo_root: Path,
    api_key: str,
    base_url: str,
    timeout_seconds: float,
    max_tokens: int,
    workers: int,
    case_ids: list[str] | None = None,
    resume: bool = False,
    retry_errors: bool = False,
    app_url: str = "",
) -> dict[str, Any]:
    selected_models = validate_models(models)
    if workers < 1 or workers > len(selected_models):
        raise PanelConfigurationError(
            "workers must be between 1 and the model count"
        )
    if not api_key.strip():
        raise PanelConfigurationError("OPENROUTER_API_KEY is required")

    dataset = load_answer_quality_dataset(dataset_path)
    dataset_sha256 = file_sha256(dataset_path)
    if dataset.locked:
        raise PanelConfigurationError(
            "annotation panel must not mutate or relabel a locked dataset"
        )
    corpus_path = resolve_corpus_path(dataset, repo_root)
    chunk_index = load_chunk_index(
        corpus_path,
        dataset.source_corpus_sha256,
    )
    selected_ids = set(case_ids or [])
    questions = [
        question
        for question in dataset.questions
        if not selected_ids or question.id in selected_ids
    ]
    unknown_ids = selected_ids - {question.id for question in questions}
    if unknown_ids:
        raise PanelConfigurationError(
            f"unknown case IDs: {sorted(unknown_ids)}"
        )
    if not questions:
        raise PanelConfigurationError("no cases selected")

    if output_dir.exists() and any(output_dir.iterdir()) and not resume:
        raise PanelConfigurationError(
            "output directory is not empty; use --resume explicitly"
        )
    records_dir = output_dir / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.json"
    run_id = datetime.now(UTC).strftime("answer-panel-%Y%m%dT%H%M%SZ")
    expected_case_ids = [question.id for question in questions]
    if resume and manifest_path.is_file():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("models") != selected_models:
            raise PanelConfigurationError(
                "resume model list differs from existing manifest"
            )
        if previous.get("case_ids") != expected_case_ids:
            raise PanelConfigurationError(
                "resume case selection differs from existing manifest"
            )
        if previous.get("dataset_sha256") != dataset_sha256:
            raise PanelConfigurationError(
                "resume dataset differs from existing manifest"
            )
        run_id = str(previous.get("run_id", "")).strip()
        if not run_id:
            raise PanelConfigurationError("existing manifest has no run_id")
    elif resume:
        existing_records = load_records(records_dir)
        previous_run_ids = {record.run_id for record in existing_records}
        if len(previous_run_ids) > 1:
            raise PanelConfigurationError(
                "existing records contain more than one run_id"
            )
        if previous_run_ids:
            run_id = previous_run_ids.pop()
    client_options = {
        "api_key": api_key,
        "base_url": base_url,
        "timeout_seconds": timeout_seconds,
        "max_tokens": max_tokens,
        "app_url": app_url,
    }

    tasks: list[tuple[AnswerQualityQuestion, str, int, Path]] = []
    skipped = 0
    messages_by_case: dict[str, list[dict[str, str]]] = {}
    for question in questions:
        messages_by_case[question.id] = build_messages(question, chunk_index)
        for index, model in enumerate(selected_models, start=1):
            record_path = (
                records_dir
                / question.id
                / record_name(index, model)
            )
            existing = _existing_record(record_path)
            if existing is not None:
                if existing.requested_model != model:
                    raise PanelConfigurationError(
                        f"record/model mismatch: {record_path}"
                    )
                if existing.status == "success" or not retry_errors:
                    skipped += 1
                    continue
            tasks.append((question, model, index, record_path))

    completed = 0
    errors = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _run_one,
                run_id=run_id,
                question=question,
                model=model,
                messages=messages_by_case[question.id],
                client_options=client_options,
            ): record_path
            for question, model, _index, record_path in tasks
        }
        for future in as_completed(futures):
            record = future.result()
            atomic_json(
                futures[future],
                record.model_dump(mode="json"),
            )
            completed += 1
            errors += int(record.status == "error")

    final_records = load_records(records_dir)
    expected_pairs = {
        (question.id, model)
        for question in questions
        for model in selected_models
    }
    actual_pairs = [
        (record.case_id, record.requested_model)
        for record in final_records
    ]
    if len(actual_pairs) != len(set(actual_pairs)):
        raise PanelConfigurationError("duplicate case/model records detected")
    unexpected_pairs = set(actual_pairs) - expected_pairs
    if unexpected_pairs:
        raise PanelConfigurationError(
            f"unexpected case/model records: {sorted(unexpected_pairs)}"
        )
    consensus_path = output_dir / "consensus-report.json"
    consensus = write_consensus_report(
        records_dir,
        selected_models,
        consensus_path,
    )
    error_record_count = sum(
        record.status == "error" for record in final_records
    )
    expected_request_count = len(expected_pairs)
    missing_record_count = len(expected_pairs - set(actual_pairs))
    manifest = {
        "schema_version": "answer-multi-llm-run-manifest-v1",
        "status": (
            "COMPLETED"
            if not error_record_count and not missing_record_count
            else "COMPLETED_WITH_ERRORS"
        ),
        "authority_review_status": "pending",
        "run_id": run_id,
        "dataset_path": str(dataset_path),
        "dataset_sha256": dataset_sha256,
        "dataset_source_sha256": dataset.source_dataset_sha256,
        "corpus_path": dataset.source_corpus_path,
        "corpus_sha256": dataset.source_corpus_sha256,
        "prompt_version": PROMPT_VERSION,
        "models": selected_models,
        "case_ids": expected_case_ids,
        "expected_request_count": expected_request_count,
        "completed_this_invocation": completed,
        "skipped_existing_records": skipped,
        "error_count_this_invocation": errors,
        "error_record_count": error_record_count,
        "missing_record_count": missing_record_count,
        "exact_consensus_count": consensus["exact_consensus_count"],
        "needs_human_adjudication_count": consensus[
            "needs_human_adjudication_count"
        ],
        "golden_locked": False,
    }
    atomic_json(manifest_path, manifest)
    return manifest


def dry_run(
    *,
    dataset_path: Path,
    models: list[str],
    repo_root: Path,
    case_ids: list[str] | None = None,
) -> dict[str, Any]:
    selected_models = validate_models(models)
    dataset = load_answer_quality_dataset(dataset_path)
    dataset_sha256 = file_sha256(dataset_path)
    corpus_path = resolve_corpus_path(dataset, repo_root)
    chunk_index = load_chunk_index(corpus_path, dataset.source_corpus_sha256)
    selected_ids = set(case_ids or [])
    questions = [
        question
        for question in dataset.questions
        if not selected_ids or question.id in selected_ids
    ]
    if selected_ids - {question.id for question in questions}:
        raise PanelConfigurationError("dry-run contains an unknown case ID")
    prompt_lengths = [
        sum(len(message["content"]) for message in build_messages(q, chunk_index))
        for q in questions
    ]
    return {
        "status": "DRY_RUN_PASS",
        "network_calls": 0,
        "question_count": len(questions),
        "model_count": len(selected_models),
        "planned_request_count": len(questions) * len(selected_models),
        "minimum_prompt_chars": min(prompt_lengths),
        "maximum_prompt_chars": max(prompt_lengths),
        "corpus_sha256": dataset.source_corpus_sha256,
        "dataset_sha256": dataset_sha256,
    }


if __name__ == "__main__":
    from evaluation.answer_quality.panel_cli import main

    raise SystemExit(main())
