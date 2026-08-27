#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${1:-$HOME/Downloads/contract-review-e2e-$STAMP.txt}"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python3}"
cd "$ROOT"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example; configure provider keys separately when Q&A generation is needed."
fi

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build postgres backend frontend
"$PYTHON_BIN" scripts/smoke_contract_review_docker.py --base-url http://127.0.0.1:8000/api --output "$OUT"
echo "E2E report: $OUT"
