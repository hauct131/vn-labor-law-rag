#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "LOI: Can Docker Engine va Docker Compose v2."
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Da tao .env tu .env.example. Hay dien API key neu can dung chuc nang hoi dap LLM."
fi

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build

for _ in $(seq 1 120); do
  if curl -fsS http://127.0.0.1:8000/api/live >/dev/null 2>&1 \
    && curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
    echo "San pham da san sang."
    echo "Frontend: http://localhost:5173/contract-reviews"
    echo "API docs: http://localhost:8000/docs"
    docker compose -f docker-compose.yml -f docker-compose.dev.yml ps
    exit 0
  fi
  sleep 1
done

echo "LOI: dich vu khong san sang sau 120 giay."
docker compose -f docker-compose.yml -f docker-compose.dev.yml ps
docker compose -f docker-compose.yml -f docker-compose.dev.yml logs --tail=120 backend frontend postgres
exit 1
