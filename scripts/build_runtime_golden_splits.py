#!/usr/bin/env python3
"""Build a locked, article-grouped dev/test split for runtime retrieval.

The split lock is scoped to retrieval evaluation. It does not approve the
underlying legal authority review, which remains pending in the release.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SPLIT_VERSION = "runtime-retrieval-v1"
SPLIT_CREATED_AT = "2026-08-01"
EXPECTED_ENABLED = 44
EXPECTED_DISABLED = 1

# Selected by connected article groups, then stratified by question category.
# Category distribution: exact=3, natural=3, single=5, multi=2.
TEST_QUESTION_IDS = frozenset({
    "r2ai_gold_001",
    "r2ai_gold_002",
    "r2ai_gold_003",
    "r2ai_gold_013",
    "r2ai_gold_014",
    "r2ai_gold_020",
    "r2ai_gold_021",
    "r2ai_gold_023",
    "r2ai_gold_024",
    "r2ai_gold_026",
    "r2ai_gold_032",
    "r2ai_gold_039",
    "r2ai_gold_041",
})


class SplitError(ValueError):
    """Raised when the source dataset cannot reproduce the locked split."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _question_ids(questions: Iterable[dict[str, Any]]) -> list[str]:
    return [str(question["id"]) for question in questions]


def _article_codes(questions: Iterable[dict[str, Any]]) -> set[str]:
    return {
        str(code)
        for question in questions
        for code in question.get("expected_article_codes", [])
        if code
    }


def _category_counts(questions: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(question.get("category") or "unknown") for question in questions)
    return dict(sorted(counts.items()))


def load_source(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SplitError(f"golden source not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SplitError(f"invalid golden JSON: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("questions"), list):
        raise SplitError("golden source must be an object with a questions list")
    return payload


def partition_questions(
    source: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    questions = source["questions"]
    ids = _question_ids(questions)
    if len(ids) != len(set(ids)):
        raise SplitError("golden source contains duplicate question ids")

    enabled = [q for q in questions if q.get("benchmark_enabled", True)]
    disabled = [q for q in questions if not q.get("benchmark_enabled", True)]
    if len(enabled) != EXPECTED_ENABLED or len(disabled) != EXPECTED_DISABLED:
        raise SplitError(
            "locked split expects 44 enabled and 1 disabled question; got "
            f"{len(enabled)} enabled and {len(disabled)} disabled"
        )

    enabled_ids = set(_question_ids(enabled))
    missing_test_ids = sorted(TEST_QUESTION_IDS - enabled_ids)
    if missing_test_ids:
        raise SplitError(
            "locked test question ids missing or disabled: "
            + ", ".join(missing_test_ids)
        )

    test = [q for q in enabled if q["id"] in TEST_QUESTION_IDS]
    dev = [q for q in enabled if q["id"] not in TEST_QUESTION_IDS]
    if len(dev) != 31 or len(test) != 13:
        raise SplitError(
            f"locked split must contain dev=31 and test=13; got {len(dev)} and {len(test)}"
        )

    overlap = sorted(_article_codes(dev) & _article_codes(test))
    if overlap:
        raise SplitError(
            "article leakage detected between dev and test: " + ", ".join(overlap)
        )
    return dev, test, disabled


def build_split_dataset(
    source: dict[str, Any],
    *,
    role: str,
    questions: list[dict[str, Any]],
    source_path: Path,
    source_sha256: str,
) -> dict[str, Any]:
    if role not in {"dev", "test"}:
        raise SplitError(f"unsupported split role: {role}")

    dataset = copy.deepcopy(source)
    dataset["schema_version"] = "golden-questions-v3-runtime-split-v1"
    dataset["dataset_status"] = (
        "retrieval_benchmark_locked_pending_authority_review"
    )
    dataset["locked"] = True
    dataset["lock_scope"] = "retrieval_labels_and_split_only"
    dataset["authority_review_status"] = "pending"
    dataset["split"] = {
        "version": SPLIT_VERSION,
        "role": role,
        "locked": True,
        "created_at": SPLIT_CREATED_AT,
        "source_path": str(source_path),
        "source_sha256": source_sha256,
        "grouping_key": "connected_expected_article_codes",
        "test_access_policy": (
            "Tune only on dev. Test evaluation requires an explicit CLI unlock "
            "and a selected dev configuration."
        ),
    }

    split_questions: list[dict[str, Any]] = []
    for question in questions:
        item = copy.deepcopy(question)
        item["benchmark_split"] = role
        item["split_version"] = SPLIT_VERSION
        split_questions.append(item)
    dataset["questions"] = split_questions

    labeling = copy.deepcopy(dataset.get("labeling", {}))
    labeling.update({
        "question_count": len(split_questions),
        "benchmark_enabled_count": len(split_questions),
        "benchmark_disabled_count": 0,
        "disabled_question_ids": [],
        "category_counts": _category_counts(split_questions),
    })
    dataset["labeling"] = labeling
    dataset["changelog"] = list(dataset.get("changelog", [])) + [
        f"Locked {role} partition for {SPLIT_VERSION}; this lock does not "
        "constitute legal authority approval."
    ]
    return dataset


def build_outputs(
    source_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    source = load_source(source_path)
    source_sha256 = sha256_file(source_path)
    dev, test, disabled = partition_questions(source)
    dev_dataset = build_split_dataset(
        source,
        role="dev",
        questions=dev,
        source_path=source_path,
        source_sha256=source_sha256,
    )
    test_dataset = build_split_dataset(
        source,
        role="test",
        questions=test,
        source_path=source_path,
        source_sha256=source_sha256,
    )

    manifest = {
        "schema_version": "runtime-retrieval-split-manifest-v1",
        "split_version": SPLIT_VERSION,
        "created_at": SPLIT_CREATED_AT,
        "locked": True,
        "lock_scope": "retrieval_labels_and_split_only",
        "authority_review_status": "pending",
        "source": {
            "path": str(source_path),
            "sha256": source_sha256,
            "schema_version": source.get("schema_version"),
            "release_id": source.get("corpus", {}).get("release_id"),
            "corpus_sha256": source.get("corpus", {}).get("sha256"),
        },
        "method": {
            "grouping_key": "connected_expected_article_codes",
            "selection": "fixed_group_aware_stratified_assignment",
            "invariant": (
                "Questions sharing any expected article, directly or "
                "transitively, must remain in the same partition."
            ),
        },
        "counts": {
            "source": len(source["questions"]),
            "dev": len(dev),
            "test": len(test),
            "disabled": len(disabled),
        },
        "category_counts": {
            "dev": _category_counts(dev),
            "test": _category_counts(test),
        },
        "question_ids": {
            "dev": _question_ids(dev),
            "test": _question_ids(test),
            "disabled": _question_ids(disabled),
        },
        "article_codes": {
            "dev": sorted(_article_codes(dev)),
            "test": sorted(_article_codes(test)),
            "cross_split_overlap": [],
        },
        "test_access_policy": {
            "tuning_allowed": False,
            "explicit_unlock_required": True,
            "selected_dev_configuration_required": True,
        },
    }
    return dev_dataset, test_dataset, manifest


def _serialized(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(_serialized(payload), encoding="utf-8")
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/evaluation/golden_questions_v3_unified_candidate.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/evaluation/splits"),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify checked-in outputs without modifying them.",
    )
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    dev, test, manifest = build_outputs(args.source)
    paths = {
        "dev": args.output_dir / "golden_v3_dev.json",
        "test": args.output_dir / "golden_v3_test.json",
        "manifest": args.output_dir / "split_manifest.json",
    }
    payloads = {"dev": dev, "test": test, "manifest": manifest}

    if args.check:
        mismatches = [
            name
            for name, path in paths.items()
            if not path.is_file()
            or path.read_text(encoding="utf-8") != _serialized(payloads[name])
        ]
        if mismatches:
            raise SplitError(
                "checked-in split outputs are missing or stale: "
                + ", ".join(mismatches)
            )
        status = "check_pass"
    else:
        for name, path in paths.items():
            _write_atomic(path, payloads[name])
        status = "written"

    return {
        "status": status,
        "split_version": SPLIT_VERSION,
        "counts": manifest["counts"],
        "category_counts": manifest["category_counts"],
        "cross_split_article_overlap": [],
        "paths": {name: str(path) for name, path in paths.items()},
    }


def main() -> None:
    result = run(build_parser().parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
