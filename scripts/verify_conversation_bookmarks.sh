#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python3}"
if [[ ! -x "$PYTHON_BIN" ]] || ! "$PYTHON_BIN" -c 'import pytest' >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

REPORT="${1:-$HOME/Downloads/conversation-bookmarks-verification.txt}"
BASE_REF="${BASE_REF:-main}"
mkdir -p "$(dirname "$REPORT")"

{
  echo '============================================================'
  echo 'SESSION AUTH + CONVERSATION HISTORY VERIFICATION'
  echo '============================================================'
  date --iso-8601=seconds
  echo

  echo '=== GIT DIFF CHECK ==='
  if git rev-parse --verify --quiet "$BASE_REF" >/dev/null; then
    git diff --check "$BASE_REF"
  else
    git diff --check
    git diff --cached --check
  fi
  echo

  echo '=== LOCKED ARTIFACTS MUST BE UNCHANGED ==='
  LOCKED_PATHS=(
    data/releases/labor-law-canonical-word-20260804-164432-candidate
    data/evaluation/splits/canonical_word_804
    data/evaluation/runtime-benchmark/canonical_word_804/locked
  )
  if git rev-parse --verify --quiet "$BASE_REF" >/dev/null; then
    LOCKED_DIFF=(git diff "$BASE_REF")
  else
    LOCKED_DIFF=(git diff)
  fi
  if "${LOCKED_DIFF[@]}" --quiet -- "${LOCKED_PATHS[@]}"; then
    echo 'PASS: locked artifacts unchanged'
  else
    echo 'FAIL: locked artifacts changed'
    "${LOCKED_DIFF[@]}" --name-only -- "${LOCKED_PATHS[@]}"
    false
  fi
  echo

  echo '=== PYTHON COMPILE ==='
  PYTHONPATH=backend "$PYTHON_BIN" -m compileall -q backend/app
  echo 'PASS'
  echo

  echo '=== FEATURE TESTS ==='
  PYTHONPATH=backend "$PYTHON_BIN" -m pytest -q \
    backend/tests/test_session_auth.py \
    backend/tests/test_conversation_history.py \
    backend/tests/test_ask_history_api.py \
    --tb=short
  echo

  echo '=== FULL BACKEND TESTS ==='
  PYTHONPATH=backend "$PYTHON_BIN" -m pytest -q backend/tests --tb=short
  echo

  echo '=== FRONTEND LINT ==='
  (
    cd frontend
    npm ci
    npm run lint
  )
  echo

  echo '=== FRONTEND BUILD ==='
  (
    cd frontend
    npm run build
  )
  echo

  echo '=== DOCKER COMPOSE CONFIG ==='
  docker compose \
    -f docker-compose.yml \
    -f docker-compose.dev.yml \
    config --quiet
  echo 'PASS'
  echo

  if [[ "${RUN_RUNTIME_SMOKE:-0}" == "1" ]]; then
    echo '=== DOCKER RUNTIME + POSTGRESQL SMOKE ==='
    docker compose \
      -f docker-compose.yml \
      -f docker-compose.dev.yml \
      up -d --build postgres qdrant backend frontend

    for attempt in $(seq 1 60); do
      if curl --fail --silent http://localhost:8000/api/live >/dev/null; then
        break
      fi
      if [[ "$attempt" == "60" ]]; then
        echo 'FAIL: backend did not become live within 120 seconds'
        docker compose \
          -f docker-compose.yml \
          -f docker-compose.dev.yml \
          logs --tail=200 backend postgres
        false
      fi
      sleep 2
    done

    python3 scripts/smoke_conversation_bookmarks.py \
      --restart-backend \
      --restart-postgres
    echo
  else
    echo '=== DOCKER RUNTIME + POSTGRESQL SMOKE ==='
    echo 'SKIP: set RUN_RUNTIME_SMOKE=1 to run the final Docker runtime gate'
    echo
  fi

  echo '=== SECRET/GENERATED FILE CHECK ==='
  if git ls-files | grep -E '(^|/)(\.env|application\.db)$' ; then
    echo 'FAIL: tracked secret/runtime file detected'
    false
  else
    echo 'PASS: no tracked .env or application.db'
  fi
  echo

  echo '=== WORKING TREE ==='
  git status --short
} 2>&1 | tee "$REPORT"

echo
echo "Báo cáo: $REPORT"
