"""Offline tests for migration, prompt rendering, provider and consensus."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

import evaluation.answer_quality.panel as panel_module
from evaluation.answer_quality.annotations import (
    AnnotationPayload,
    AnnotationRecord,
)
from evaluation.answer_quality.consensus import build_consensus_report
from evaluation.answer_quality.migrate_candidates import (
    DEFAULT_CHUNKS,
    DEFAULT_OUTPUT,
    DEFAULT_SOURCE,
    build_dataset,
    run as run_migration,
)
from evaluation.answer_quality.list_models import fetch_compatible_models
from evaluation.answer_quality.openrouter import (
    OpenRouterAnnotationClient,
    OpenRouterAnnotationError,
    OpenRouterAnnotationResult,
)
from evaluation.answer_quality.panel import dry_run, run_panel
from evaluation.answer_quality.panel_support import (
    AnnotationReferenceError,
    PanelConfigurationError,
    validate_annotation_references,
    validate_models,
)
from evaluation.answer_quality.prompt import (
    build_messages,
    load_chunk_index,
)
from evaluation.answer_quality.schema import (
    AnswerQualityQuestion,
    load_answer_quality_dataset,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS = ["vendor-a/model-a", "vendor-b/model-b", "vendor-c/model-c"]


def annotation_payload(
    *,
    text: str = "Người sử dụng lao động phải quy định ca làm việc.",
    claim_id: str = "c1",
) -> dict[str, object]:
    return {
        "expected_status": "answerable",
        "required_claims": [
            {
                "claim_id": claim_id,
                "text": text,
                "supported_by_article_codes": ["20.2.TT.3.8"],
                "supported_by_chunk_ids": [
                    "c04d9569-184f-5daa-9c59-dd53087c9995"
                ],
            }
        ],
        "forbidden_claims": [],
        "review_notes": "Chỉ trích xuất từ bằng chứng.",
    }


def make_record(
    model: str,
    payload: dict[str, object] | None,
    *,
    error: str = "",
) -> AnnotationRecord:
    now = datetime.now(UTC)
    return AnnotationRecord(
        run_id="run-1",
        case_id="labor_candidate_046",
        requested_model=model,
        resolved_model=model,
        response_id="response-1",
        prompt_sha256="a" * 64,
        request_sha256="b" * 64,
        started_at=now,
        finished_at=now,
        latency_ms=1,
        status="error" if error else "success",
        raw_content=json.dumps(payload) if payload is not None else "",
        parsed_annotation=(
            AnnotationPayload.model_validate(payload)
            if payload is not None and not error
            else None
        ),
        error=error,
    )


def test_real_heldout_migration_is_reproducible_and_still_unlocked() -> None:
    dataset = build_dataset(
        REPO_ROOT / DEFAULT_SOURCE,
        REPO_ROOT / DEFAULT_CHUNKS,
        repo_root=REPO_ROOT,
    )

    assert len(dataset.questions) == 20
    assert dataset.locked is False
    assert dataset.authority_review_status.value == "pending"
    assert all(not item.required_claims for item in dataset.questions)
    assert all(not item.benchmark_enabled for item in dataset.questions)
    report = run_migration(
        REPO_ROOT / DEFAULT_SOURCE,
        REPO_ROOT / DEFAULT_CHUNKS,
        REPO_ROOT / DEFAULT_OUTPUT,
        repo_root=REPO_ROOT,
        check=True,
    )
    assert report["status"] == "check_pass"


def test_dry_run_builds_60_requests_without_network() -> None:
    report = dry_run(
        dataset_path=REPO_ROOT / DEFAULT_OUTPUT,
        models=MODELS,
        repo_root=REPO_ROOT,
    )

    assert report["status"] == "DRY_RUN_PASS"
    assert report["network_calls"] == 0
    assert report["question_count"] == 20
    assert report["planned_request_count"] == 60
    assert report["maximum_prompt_chars"] > report["minimum_prompt_chars"]


def test_panel_requires_three_distinct_model_organizations() -> None:
    with pytest.raises(PanelConfigurationError, match="distinct"):
        validate_models([
            "vendor-a/model-a",
            "vendor-a/model-b",
            "vendor-c/model-c",
        ])


def test_prompt_uses_full_hash_bound_chunk_content() -> None:
    dataset = load_answer_quality_dataset(REPO_ROOT / DEFAULT_OUTPUT)
    chunks = load_chunk_index(
        REPO_ROOT / dataset.source_corpus_path,
        dataset.source_corpus_sha256,
    )
    messages = build_messages(dataset.questions[0], chunks)
    user_prompt = messages[1]["content"]

    assert "EVIDENCE PACKET" in user_prompt
    assert dataset.questions[0].evidence_chunk_ids[0] in user_prompt
    assert chunks[dataset.questions[0].evidence_chunk_ids[0]][
        "content"
    ] in user_prompt


def test_openrouter_uses_exact_model_and_strict_structured_output() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "generation-1",
                "model": "vendor-a/model-a",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(
                                annotation_payload(),
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "cost": 0.01,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenRouterAnnotationClient(
            api_key="test-secret",
            http_client=http_client,
        )
        result = client.annotate(
            model="vendor-a/model-a",
            messages=[{"role": "user", "content": "test"}],
        )

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "vendor-a/model-a"
    assert "models" not in body
    assert body["temperature"] == 0
    assert body["provider"] == {"require_parameters": True}
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert captured["authorization"] == "Bearer test-secret"
    assert result.resolved_model == "vendor-a/model-a"
    assert result.parsed_annotation.required_claims[0].claim_id == "c1"


def test_openrouter_rejects_invalid_structured_content() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "generation-1",
                "model": "vendor-a/model-a",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "not JSON",
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenRouterAnnotationClient(
            api_key="test-secret",
            http_client=http_client,
        )
        with pytest.raises(
            OpenRouterAnnotationError,
            match="violates annotation schema",
        ):
            client.annotate(
                model="vendor-a/model-a",
                messages=[{"role": "user", "content": "test"}],
            )


def test_provider_error_record_redacts_api_key() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"message": "invalid key test-secret"}},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        client = OpenRouterAnnotationClient(
            api_key="test-secret",
            http_client=http_client,
        )
        with pytest.raises(OpenRouterAnnotationError) as captured:
            client.annotate(
                model="vendor-a/model-a",
                messages=[{"role": "user", "content": "test"}],
            )

    error = captured.value
    assert "test-secret" not in str(error)
    assert error.provider_response == {
        "error": {"message": "invalid key [REDACTED]"}
    }


def test_model_discovery_filters_current_metadata_without_guessing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["supported_parameters"] == (
            "structured_outputs"
        )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "vendor-a/free-model",
                        "name": "Free model",
                        "context_length": 32000,
                        "architecture": {"output_modalities": ["text"]},
                        "supported_parameters": [
                            "structured_outputs",
                            "temperature",
                        ],
                        "pricing": {
                            "prompt": "0",
                            "completion": "0",
                            "request": "0",
                        },
                    },
                    {
                        "id": "vendor-b/no-schema",
                        "name": "No schema",
                        "context_length": 32000,
                        "architecture": {"output_modalities": ["text"]},
                        "supported_parameters": ["temperature"],
                        "pricing": {
                            "prompt": "0",
                            "completion": "0",
                            "request": "0",
                        },
                    },
                    {
                        "id": "vendor-c/paid-model",
                        "name": "Paid model",
                        "context_length": 32000,
                        "architecture": {"output_modalities": ["text"]},
                        "supported_parameters": ["structured_outputs"],
                        "pricing": {
                            "prompt": "0.000001",
                            "completion": "0",
                            "request": "0",
                        },
                    },
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        models = fetch_compatible_models(
            free_only=True,
            http_client=client,
        )

    assert [item["id"] for item in models] == ["vendor-a/free-model"]


def test_panel_rejects_claim_reference_outside_evidence() -> None:
    question = AnswerQualityQuestion.model_validate({
        "id": "case-1",
        "question": "Quy định nào được áp dụng?",
        "category": "test",
        "question_type": "direct_rule",
        "difficulty": "easy",
        "expected_status": "answerable",
        "expected_article_codes": ["A"],
        "expected_article_ids": ["article-a"],
        "evidence_chunk_ids": ["chunk-a"],
        "evidence_excerpt": "Bằng chứng.",
    })
    annotation = AnnotationPayload.model_validate({
        "expected_status": "answerable",
        "required_claims": [
            {
                "claim_id": "c1",
                "text": "Claim",
                "supported_by_article_codes": ["A"],
                "supported_by_chunk_ids": ["chunk-outside"],
            }
        ],
        "forbidden_claims": [],
        "review_notes": "",
    })

    with pytest.raises(AnnotationReferenceError, match="outside evidence"):
        validate_annotation_references(question, annotation)


def test_consensus_ignores_claim_ids_but_not_claim_meaning() -> None:
    equivalent = [
        make_record(model, annotation_payload(claim_id=f"c{index}"))
        for index, model in enumerate(MODELS, start=1)
    ]
    agreed = build_consensus_report(equivalent, MODELS)
    assert agreed["exact_consensus_count"] == 1
    assert agreed["cases"][0]["decision"] == "multi_llm_candidate"
    assert agreed["authority_review_status"] == "pending"

    changed = list(equivalent)
    changed[-1] = make_record(
        MODELS[-1],
        annotation_payload(text="Một nội dung pháp lý khác."),
    )
    disagreed = build_consensus_report(changed, MODELS)
    assert disagreed["exact_consensus_count"] == 0
    assert disagreed["needs_human_adjudication_count"] == 1


def test_provider_error_forces_human_adjudication() -> None:
    records = [
        make_record(MODELS[0], annotation_payload()),
        make_record(MODELS[1], annotation_payload()),
        make_record(MODELS[2], None, error="provider unavailable"),
    ]

    report = build_consensus_report(records, MODELS)

    assert report["cases"][0]["exact_consensus"] is False
    assert report["cases"][0]["model_errors"] == {
        MODELS[2]: "provider unavailable"
    }


def test_panel_writes_atomic_records_and_resumes_without_new_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeClient:
        def __init__(self, **_options: object) -> None:
            pass

        def annotate(
            self,
            *,
            model: str,
            messages: list[dict[str, str]],
        ) -> OpenRouterAnnotationResult:
            calls.append(model)
            payload = AnnotationPayload.model_validate(annotation_payload())
            return OpenRouterAnnotationResult(
                requested_model=model,
                resolved_model=model,
                response_id=f"response-{len(calls)}",
                raw_content=payload.model_dump_json(),
                parsed_annotation=payload,
                provider_response={"model": model, "messages_seen": len(messages)},
                request_sha256="c" * 64,
            )

    monkeypatch.setattr(
        panel_module,
        "OpenRouterAnnotationClient",
        FakeClient,
    )
    output_dir = tmp_path / "panel"
    first = run_panel(
        dataset_path=REPO_ROOT / DEFAULT_OUTPUT,
        output_dir=output_dir,
        models=MODELS,
        repo_root=REPO_ROOT,
        api_key="test-key",
        base_url="https://example.invalid/api/v1",
        timeout_seconds=10,
        max_tokens=1000,
        workers=3,
        case_ids=["labor_candidate_046"],
    )

    assert first["status"] == "COMPLETED"
    assert first["expected_request_count"] == 3
    assert first["exact_consensus_count"] == 1
    assert first["golden_locked"] is False
    assert len(list((output_dir / "records").glob("*/*.json"))) == 3
    assert len(calls) == 3

    resumed = run_panel(
        dataset_path=REPO_ROOT / DEFAULT_OUTPUT,
        output_dir=output_dir,
        models=MODELS,
        repo_root=REPO_ROOT,
        api_key="test-key",
        base_url="https://example.invalid/api/v1",
        timeout_seconds=10,
        max_tokens=1000,
        workers=3,
        case_ids=["labor_candidate_046"],
        resume=True,
    )

    assert resumed["skipped_existing_records"] == 3
    assert resumed["completed_this_invocation"] == 0
    assert resumed["run_id"] == first["run_id"]
    assert len(calls) == 3
