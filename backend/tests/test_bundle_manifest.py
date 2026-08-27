"""Regression checks for the project-level canonical release declaration."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_bundle_manifest_matches_canonical_804_release() -> None:
    bundle = json.loads(
        (PROJECT_ROOT / "BUNDLE_MANIFEST.json").read_text(encoding="utf-8")
    )
    release_dir = PROJECT_ROOT / "data/releases" / bundle["release_id"]
    release = json.loads(
        (release_dir / "manifest.json").read_text(encoding="utf-8")
    )
    chunks_path = release_dir / "canonical_chunks.jsonl"
    chunk_count = sum(
        1 for line in chunks_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    chunk_sha256 = hashlib.sha256(chunks_path.read_bytes()).hexdigest()

    assert bundle["release_id"] == "labor-law-canonical-word-20260804-164432-candidate"
    assert bundle["counts"]["chunks"] == 804 == release["counts"]["chunks"]
    assert bundle["hashes"]["canonical_chunks_sha256"] == chunk_sha256
    assert release["hashes"]["canonical_chunks_sha256"] == chunk_sha256
    assert bundle["gates"]["authority_review"] == "pending"
    assert bundle["gates"]["production_publishable"] is False
    assert all((PROJECT_ROOT / path).exists() for path in bundle["primary_files"])
    assert all(
        "data/evaluation/splits/golden_v3_" not in path
        for path in bundle["primary_files"]
    )
    assert (
        "data/evaluation/splits/canonical_word_804/golden_v3_dev.json"
        in bundle["primary_files"]
    )


def test_active_make_targets_bind_canonical_804_and_lock_test_reruns() -> None:
    makefile = (PROJECT_ROOT / "Makefile.eval.inc").read_text(encoding="utf-8")

    assert (
        "data/releases/labor-law-canonical-word-20260804-164432-candidate"
        in makefile
    )
    assert "UNIFIED_EXPECTED_CHUNKS ?= 804" in makefile
    assert (
        "UNIFIED_EXPECTED_SHA256 ?= "
        "fdbec539efbfb3f4aa3cb3962046321e3"
        "a402150d93516ef4256934972c70307"
        in makefile
    )
    assert (
        "CANONICAL_SPLIT_DIR ?= data/evaluation/splits/canonical_word_804"
        in makefile
    )
    assert "post-test tuning is locked" in makefile
    assert "locked test was already run once" in makefile


def test_retired_833_runtime_requires_explicit_opt_in() -> None:
    legacy_script = (
        PROJECT_ROOT / "scripts/index_and_evaluate_unified.sh"
    ).read_text(encoding="utf-8")

    assert "ALLOW_LEGACY_UNIFIED_833" in legacy_script
    assert "scripts/verify_final_release.sh" in legacy_script
