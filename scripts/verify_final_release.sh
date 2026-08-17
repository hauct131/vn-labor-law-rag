#!/usr/bin/env bash
""":"
Full technical verification for canonical Word 804 plus application features.

The locked retrieval test is never rerun. Its immutable evidence is verified
by checksum and manifest binding instead.
":"""

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARTIFACT_DIR="${1:-$HOME/Downloads/vn-labor-law-rag-final-$STAMP}"
REPORT="$ARTIFACT_DIR/final-verification.txt"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python3}"
NPM_CACHE_DIR="${NPM_CACHE_DIR:-$ARTIFACT_DIR/npm-cache}"
NPM_LOG_DIR="${NPM_LOG_DIR:-$ARTIFACT_DIR/npm-logs}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)

mkdir -p "$ARTIFACT_DIR"
exec > >(tee "$REPORT") 2>&1
trap 'code=$?; echo "FINAL VERIFICATION: FAIL (exit=$code, line=$LINENO)"; echo "Report: $REPORT"; exit "$code"' ERR

cd "$ROOT"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: project Python not found: $PYTHON_BIN"
  echo "Create the project venv with Python 3.11/3.12 and install backend/requirements.txt."
  exit 2
fi

echo "============================================================"
echo "VN LABOR LAW RAG - FINAL TECHNICAL VERIFICATION"
echo "============================================================"
date --iso-8601=seconds
echo "Commit: $(git rev-parse HEAD)"
echo "Branch: $(git branch --show-current)"
echo "Python: $($PYTHON_BIN --version 2>&1)"
echo "Artifacts: $ARTIFACT_DIR"

echo
echo "===== 1. SOURCE INTEGRITY ====="
git diff --check
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "ERROR: tracked working tree is not clean"
  git status --short
  exit 3
fi
if git grep -n -E '^(<<<<<<<|=======|>>>>>>>)' -- ':!*.md'; then
  echo "ERROR: unresolved merge marker found"
  exit 3
fi
if git ls-files | grep -E '(^|/)(\.env|application\.db)$'; then
  echo "ERROR: tracked secret/runtime database detected"
  exit 3
fi
echo "PASS: clean tracked tree, no merge markers, no tracked secrets"

echo
echo "===== 2. CANONICAL WORD 804 LOCKED ARTIFACTS ====="
(
  cd data/releases/labor-law-canonical-word-20260804-164432-candidate
  sha256sum -c SHA256SUMS.txt
)
(
  cd data/evaluation/splits/canonical_word_804
  sha256sum -c SHA256SUMS.txt
)
(
  cd data/evaluation/runtime-benchmark/canonical_word_804/locked
  sha256sum -c SHA256SUMS.txt
)

PYTHONPATH=".:backend:${PYTHONPATH:-}" "$PYTHON_BIN" \
  scripts/build_runtime_golden_splits.py \
  --output-dir data/evaluation/splits/canonical_word_804 \
  --check

"$PYTHON_BIN" - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path.cwd()
bundle = json.loads((root / "BUNDLE_MANIFEST.json").read_text(encoding="utf-8"))
final_manifest = (
    root
    / "data/evaluation/runtime-benchmark/canonical_word_804/FINAL_MANIFEST.json"
)
final = json.loads(final_manifest.read_text(encoding="utf-8"))
release_dir = root / "data/releases" / bundle["release_id"]
chunks = release_dir / "canonical_chunks.jsonl"
digest = hashlib.sha256(chunks.read_bytes()).hexdigest()
count = sum(1 for line in chunks.open(encoding="utf-8") if line.strip())

assert count == 804 == bundle["counts"]["chunks"] == final["corpus"]["chunk_count"]
assert digest == "fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307"
assert digest == bundle["hashes"]["canonical_chunks_sha256"] == final["corpus"]["sha256"]
assert final["locked_test_policy"]["test_split_consumed"] is True
assert final["locked_test_policy"]["post_test_tuning_allowed"] is False
assert bundle["gates"]["authority_review"] == "pending"
assert bundle["gates"]["production_publishable"] is False
assert all((root / path).is_file() for path in bundle["primary_files"])
print("PASS: corpus, bundle, golden and locked retrieval evidence are mutually bound")
print("NOTICE: authority review remains pending; this is not a legal production approval")
PY

echo
echo "===== 3. BACKEND STATIC AND TEST SUITE ====="
PYTHONPATH=".:backend:${PYTHONPATH:-}" "$PYTHON_BIN" -m compileall -q backend/app scripts
PYTHONPATH=".:backend:${PYTHONPATH:-}" "$PYTHON_BIN" -m pytest -q backend/tests --tb=short

echo
echo "===== 4. FRONTEND QUALITY GATES ====="
(
  cd frontend
  npm --cache "$NPM_CACHE_DIR" --logs-dir "$NPM_LOG_DIR" ci
  npm --cache "$NPM_CACHE_DIR" --logs-dir "$NPM_LOG_DIR" run lint
  npm --cache "$NPM_CACHE_DIR" --logs-dir "$NPM_LOG_DIR" run build
  npm --cache "$NPM_CACHE_DIR" --logs-dir "$NPM_LOG_DIR" audit
)

echo
echo "===== 5. DOCKER COMPOSE AND APPLICATION E2E ====="
command -v docker >/dev/null
docker version
docker compose version
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
export COMPOSE_BAKE="${COMPOSE_BAKE:-false}"
"${COMPOSE[@]}" config --quiet

"${COMPOSE[@]}" up -d qdrant postgres

PYTHONPATH=".:backend:${PYTHONPATH:-}" "$PYTHON_BIN" \
  -m backend.app.ingestion.index_qdrant \
  --chunks data/releases/labor-law-canonical-word-20260804-164432-candidate/canonical_chunks.jsonl \
  --audit-summary data/releases/labor-law-canonical-word-20260804-164432-candidate/e5_audit/summary.json \
  --collection labor_law_dev \
  --expected-chunks 804 \
  --expected-sha256 fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307 \
  --dense-model intfloat/multilingual-e5-large \
  --dense-vector-name dense \
  --dense-size 1024 \
  --sparse-model Qdrant/bm25 \
  --sparse-vector-name sparse \
  --resume \
  --summary-output "$ARTIFACT_DIR/qdrant-index.json"

PYTHONPATH=".:backend:${PYTHONPATH:-}" "$PYTHON_BIN" \
  -m backend.app.ingestion.index_qdrant \
  --chunks data/releases/labor-law-canonical-word-20260804-164432-candidate/canonical_chunks.jsonl \
  --audit-summary data/releases/labor-law-canonical-word-20260804-164432-candidate/e5_audit/summary.json \
  --collection labor_law_dev \
  --expected-chunks 804 \
  --expected-sha256 fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307 \
  --dense-model intfloat/multilingual-e5-large \
  --dense-vector-name dense \
  --dense-size 1024 \
  --sparse-model Qdrant/bm25 \
  --sparse-vector-name sparse \
  --verify-only \
  --summary-output "$ARTIFACT_DIR/qdrant-verify.json"

PYTHON_BIN="$PYTHON_BIN" bash scripts/verify_contract_review_e2e.sh \
  "$ARTIFACT_DIR/contract-review-docker-e2e.json"

"$PYTHON_BIN" scripts/smoke_conversation_bookmarks.py \
  --restart-backend \
  --restart-postgres \
  | tee "$ARTIFACT_DIR/conversation-bookmarks-e2e.txt"

curl --fail --silent http://localhost:8000/api/ready \
  | tee "$ARTIFACT_DIR/runtime-ready.json"
echo

echo
echo "===== 6. REAL BROWSER E2E ====="
"$PYTHON_BIN" -c 'import playwright'
"$PYTHON_BIN" scripts/smoke_contract_review_browser.py \
  --frontend-url http://localhost:5173 \
  --output "$ARTIFACT_DIR/contract-review-browser-e2e.json" \
  --screenshot "$ARTIFACT_DIR/contract-review-browser-final.png"

echo
echo "===== 7. FINAL STATE ====="
git diff --check
git status --short

echo
echo "FINAL VERIFICATION: PASS"
echo "Locked test was verified, not rerun."
echo "Authority review remains pending."
echo "Report: $REPORT"
