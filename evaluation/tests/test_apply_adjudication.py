from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.answer_quality.apply_adjudication import (
    DEFAULT_ADJUDICATION,
    DEFAULT_DATASET,
    DEFAULT_OUTPUT,
    AdjudicationApplicationError,
    build_reviewed_dataset,
    run,
)
from evaluation.answer_quality.schema import DatasetStatus, LabelStatus


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_real_adjudication_builds_unlocked_reviewed_dataset() -> None:
    dataset = build_reviewed_dataset(
        REPO_ROOT / DEFAULT_DATASET,
        REPO_ROOT / DEFAULT_ADJUDICATION,
        repo_root=REPO_ROOT,
    )

    assert dataset.dataset_status == DatasetStatus.MULTI_LLM_REVIEWED
    assert dataset.locked is False
    assert len(dataset.questions) == 20
    assert sum(len(item.required_claims) for item in dataset.questions) == 118
    assert sum(len(item.forbidden_claims) for item in dataset.questions) == 33
    assert all(
        item.label_status == LabelStatus.MULTI_LLM_CANDIDATE
        for item in dataset.questions
    )
    assert all(item.reviewer == "" for item in dataset.questions)
    assert all(item.benchmark_enabled is False for item in dataset.questions)


def test_committed_reviewed_dataset_is_reproducible() -> None:
    report = run(
        REPO_ROOT / DEFAULT_DATASET,
        REPO_ROOT / DEFAULT_ADJUDICATION,
        REPO_ROOT / DEFAULT_OUTPUT,
        repo_root=REPO_ROOT,
        check=True,
    )

    assert report["status"] == "check_pass"
    assert report["question_count"] == 20
    assert report["benchmark_enabled_count"] == 0


def test_adjudication_requires_exact_case_coverage(tmp_path: Path) -> None:
    payload = json.loads(
        (REPO_ROOT / DEFAULT_ADJUDICATION).read_text(encoding="utf-8")
    )
    payload["cases"].pop()
    adjudication = tmp_path / "incomplete.json"
    adjudication.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(
        AdjudicationApplicationError,
        match="coverage mismatch",
    ):
        build_reviewed_dataset(
            REPO_ROOT / DEFAULT_DATASET,
            adjudication,
            repo_root=REPO_ROOT,
        )


def test_adjudication_rejects_reference_outside_case_evidence(
    tmp_path: Path,
) -> None:
    payload = json.loads(
        (REPO_ROOT / DEFAULT_ADJUDICATION).read_text(encoding="utf-8")
    )
    payload["cases"][0]["required_claims"][0][
        "supported_by_chunk_ids"
    ] = ["not-an-allowed-chunk"]
    adjudication = tmp_path / "invalid-reference.json"
    adjudication.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(
        AdjudicationApplicationError,
        match="chunk outside allowlist",
    ):
        build_reviewed_dataset(
            REPO_ROOT / DEFAULT_DATASET,
            adjudication,
            repo_root=REPO_ROOT,
        )


def test_adjudication_cannot_enable_or_lock_benchmark() -> None:
    dataset = build_reviewed_dataset(
        REPO_ROOT / DEFAULT_DATASET,
        REPO_ROOT / DEFAULT_ADJUDICATION,
        repo_root=REPO_ROOT,
    )

    assert dataset.locked is False
    assert dataset.dataset_status != DatasetStatus.LOCKED
    assert not any(item.benchmark_enabled for item in dataset.questions)
