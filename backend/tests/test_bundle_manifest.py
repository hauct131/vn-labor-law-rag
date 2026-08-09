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
