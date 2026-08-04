#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

PYTHON_BIN="${CANONICAL_PYTHON:-$REPO_DIR/.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

RELEASE_DIR="data/releases/labor-law-canonical-20260727-candidate"
SOURCE_CHUNKS="data/releases/labor-law-2026-07-28-provenance-rebuild-20260728T095702Z/chunks.jsonl"
SOURCE_E5_AUDIT="data/quality/unified_e5_token_audit/summary.json"
CANONICAL_E5_AUDIT="$RELEASE_DIR/e5_audit/summary.json"
SOURCE_PREFLIGHT="/tmp/labor_law_source_qdrant_preflight.json"
CANONICAL_PREFLIGHT="/tmp/labor_law_canonical_qdrant_preflight.json"
READINESS="$RELEASE_DIR/QDRANT_BLUE_GREEN_READINESS.json"

if [[ ! -f "$CANONICAL_E5_AUDIT" ]]; then
  echo "ERROR: missing canonical E5 audit: $CANONICAL_E5_AUDIT" >&2
  echo "Run scripts/audit_e5_token_lengths.py for canonical_chunks.jsonl first." >&2
  exit 1
fi

"$PYTHON_BIN" scripts/complete_canonical_corpus.py \
  --e5-audit-summary "$CANONICAL_E5_AUDIT"

"$PYTHON_BIN" scripts/audit_golden_dataset.py \
  --golden data/evaluation/golden_questions_v3_canonical_20260727.json \
  --chunks "$RELEASE_DIR/canonical_chunks.jsonl" \
  --report "$RELEASE_DIR/golden_strict_audit.json" \
  --strict

"$PYTHON_BIN" scripts/evaluate_lexical_baseline.py \
  --golden data/evaluation/golden_questions_v3_canonical_20260727.json \
  --chunks "$RELEASE_DIR/canonical_chunks.jsonl" \
  --output "$RELEASE_DIR/bm25_regression.json" \
  --k 1 3 5 10

SOURCE_SHA="$(sha256sum "$SOURCE_CHUNKS" | cut -d' ' -f1)"
"$PYTHON_BIN" backend/app/ingestion/index_qdrant.py \
  --chunks "$SOURCE_CHUNKS" \
  --audit-summary "$SOURCE_E5_AUDIT" \
  --expected-chunks 833 \
  --expected-sha256 "$SOURCE_SHA" \
  --collection labor_law_bluegreen_preflight_20260727 \
  --dry-run \
  --summary-output "$SOURCE_PREFLIGHT"

CANONICAL_SHA="$(sha256sum "$RELEASE_DIR/canonical_chunks.jsonl" | cut -d' ' -f1)"
# Negative preflight: an audit for the old source corpus must never authorize
# the canonical corpus.
set +e
"$PYTHON_BIN" backend/app/ingestion/index_qdrant.py \
  --chunks "$RELEASE_DIR/canonical_chunks.jsonl" \
  --audit-summary "$SOURCE_E5_AUDIT" \
  --expected-chunks 844 \
  --expected-sha256 "$CANONICAL_SHA" \
  --collection labor_law_canonical_20260727 \
  --dry-run \
  --summary-output "$CANONICAL_PREFLIGHT"
CANONICAL_PREFLIGHT_EXIT=$?
set -e
if [[ "$CANONICAL_PREFLIGHT_EXIT" -eq 0 ]]; then
  echo "ERROR: canonical preflight unexpectedly accepted an audit bound to the old corpus" >&2
  exit 1
fi

# Positive preflight: the exact audit bound to the canonical bytes must pass.
"$PYTHON_BIN" backend/app/ingestion/index_qdrant.py \
  --chunks "$RELEASE_DIR/canonical_chunks.jsonl" \
  --audit-summary "$CANONICAL_E5_AUDIT" \
  --expected-chunks 844 \
  --expected-sha256 "$CANONICAL_SHA" \
  --collection labor_law_canonical_20260727 \
  --dry-run \
  --summary-output "$CANONICAL_PREFLIGHT"

"$PYTHON_BIN" - \
  "$SOURCE_PREFLIGHT" \
  "$CANONICAL_PREFLIGHT" \
  "$RELEASE_DIR/manifest.json" \
  "$RELEASE_DIR/canonical_chunks.jsonl" \
  "$READINESS" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

source_path, canonical_path, manifest_path, chunks_path, output_path = map(
    Path, sys.argv[1:]
)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"ERROR: missing preflight/readiness input: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ERROR: invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit(f"ERROR: expected a JSON object in {path}")
    return value


source = load_json(source_path)
canonical = load_json(canonical_path)
manifest = load_json(manifest_path)

for label, report in (("source", source), ("canonical", canonical)):
    if report.get("status") != "dry_run_success":
        raise SystemExit(f"ERROR: {label} preflight did not pass")
    if report.get("exact_e5_audit_bound") is not True:
        raise SystemExit(f"ERROR: {label} preflight is not bound to an exact E5 audit")
    if report.get("qdrant_written") is not False:
        raise SystemExit(f"ERROR: {label} dry-run unexpectedly reports a Qdrant write")

expected_count = manifest.get("counts", {}).get("canonical_chunks")
if canonical.get("chunk_count") != expected_count:
    raise SystemExit(
        "ERROR: canonical preflight chunk count does not match manifest: "
        f"{canonical.get('chunk_count')} != {expected_count}"
    )

if manifest.get("gates", {}).get("exact_e5_audit_passed") is not True:
    raise SystemExit("ERROR: manifest exact E5 audit gate is not true")
if manifest.get("technical_candidate_passed") is not True:
    raise SystemExit("ERROR: manifest technical candidate gate is not true")

digest = hashlib.sha256()
with chunks_path.open("rb") as handle:
    for block in iter(lambda: handle.read(65536), b""):
        digest.update(block)
expected_sha = digest.hexdigest()
if canonical.get("corpus_sha256") != expected_sha:
    raise SystemExit(
        "ERROR: canonical preflight SHA-256 does not match canonical chunks: "
        f"{canonical.get('corpus_sha256')} != {expected_sha}"
    )

readiness = {
    "schema_version": "qdrant-blue-green-readiness-v1",
    "release_id": manifest.get("release_id"),
    "source_candidate_preflight": {
        "collection": source.get("collection"),
        "chunk_count": source.get("chunk_count"),
        "corpus_sha256": source.get("corpus_sha256"),
        "exact_e5_audit_bound": True,
        "dry_run_status": "success",
        "qdrant_written": False,
    },
    "canonical_candidate_preflight": {
        "collection": canonical.get("collection"),
        "chunk_count": canonical.get("chunk_count"),
        "corpus_sha256": canonical.get("corpus_sha256"),
        "exact_e5_audit_bound": True,
        "dry_run_status": "success",
        "qdrant_written": False,
        "alias_switched": False,
    },
    "required_before_alias_switch": [
        "Obtain authorized legal-effect approval bound to the final manifest SHA-256.",
        "Index a new canonical candidate collection and verify its exact point count and golden regression.",
        "Switch labor_law_active only after all production release gates pass; retain labor_law for rollback.",
    ],
}

output_path.parent.mkdir(parents=True, exist_ok=True)
tmp_path = output_path.with_name(f".{output_path.name}.tmp")
tmp_path.write_text(
    json.dumps(readiness, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
os.replace(tmp_path, output_path)
print(f"Updated Qdrant readiness: {output_path}")
PY

CHECKSUM_TMP="$(mktemp)"
(
  cd "$RELEASE_DIR"
  find . -maxdepth 1 -type f ! -name SHA256SUMS.txt -printf '%f\0' \
    | sort -z \
    | xargs -0 sha256sum
) > "$CHECKSUM_TMP"
mv "$CHECKSUM_TMP" "$RELEASE_DIR/SHA256SUMS.txt"

"$PYTHON_BIN" -m pytest -q -m 'not integration'

(
  cd "$RELEASE_DIR"
  sha256sum -c SHA256SUMS.txt
)

jq '{
  release_id,
  law_as_of,
  counts,
  technical_candidate_passed,
  production_publishable,
  gates
}' "$RELEASE_DIR/manifest.json"
