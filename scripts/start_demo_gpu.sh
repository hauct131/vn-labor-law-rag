#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$SCRIPT_DIR/docker-compose.yml" && -d "$SCRIPT_DIR/backend" ]]; then
  PROJECT_ROOT="$SCRIPT_DIR"
elif [[ -f "$SCRIPT_DIR/../docker-compose.yml" && -d "$SCRIPT_DIR/../backend" ]]; then
  PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
else
  echo "ERROR: Không xác định được thư mục gốc project." >&2
  echo "Đặt script ở thư mục gốc hoặc thư mục scripts/ của project." >&2
  exit 1
fi

cd "$PROJECT_ROOT"

MODE="${1:-start}"

info() {
  printf '\n[%s] %s\n' "INFO" "$*"
}

error() {
  printf '\n[%s] %s\n' "ERROR" "$*" >&2
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    error "Thiếu lệnh bắt buộc: $1"
    exit 1
  fi
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local attempts="${3:-30}"

  for ((i = 1; i <= attempts; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      echo "$name: OK"
      return 0
    fi
    sleep 1
  done

  error "$name không phản hồi tại $url sau ${attempts}s."
  return 1
}

show_status() {
  info "Docker services"
  docker compose ps || true

  info "GPU host"
  nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu \
    --format=csv,noheader 2>/dev/null || true

  info "Health endpoints"
  if curl -fsS http://localhost:6333/healthz >/dev/null 2>&1; then
    echo "Qdrant: OK"
  else
    echo "Qdrant: NOT READY"
  fi

  if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then
    echo "Backend: OK"
  else
    echo "Backend: NOT READY"
  fi

  if curl -fsS http://localhost:5173 >/dev/null 2>&1; then
    echo "Frontend: OK"
  else
    echo "Frontend: NOT READY"
  fi
}

stop_demo() {
  info "Dừng các container demo"
  docker compose stop backend frontend qdrant postgres || true

  echo
  echo "Nếu backend host còn chạy ở terminal khác, nhấn Ctrl+C tại terminal đó."
  echo "Không dùng 'docker compose down -v' vì lệnh này xóa volume dữ liệu."
}

case "$MODE" in
  check)
    require_command docker
    require_command curl
    require_command nvidia-smi
    show_status
    exit 0
    ;;
  stop)
    require_command docker
    stop_demo
    exit 0
    ;;
  start)
    ;;
  *)
    echo "Cách dùng: $0 [start|check|stop]" >&2
    exit 2
    ;;
esac

require_command docker
require_command curl
require_command nvidia-smi

if [[ ! -x ".venv/bin/python" ]]; then
  error "Không tìm thấy Python tại $PROJECT_ROOT/.venv/bin/python"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  error "Docker Compose không khả dụng."
  exit 1
fi

info "Kiểm tra NVIDIA GPU trên host"
nvidia-smi --query-gpu=name,memory.total,memory.used,utilization.gpu \
  --format=csv,noheader

info "Dừng backend container để giải phóng cổng 8000"
docker compose stop backend >/dev/null 2>&1 || true

if curl -fsS http://localhost:8000/api/health >/dev/null 2>&1; then
  error "Cổng 8000 đang có một backend khác hoạt động."
  echo "Hãy dừng process đó trước khi chạy lại script." >&2
  exit 1
fi

info "Khởi động Qdrant và PostgreSQL"
docker compose up -d qdrant postgres

info "Khởi động frontend mà không kéo backend container"
docker compose up -d --no-deps frontend

info "Chờ Qdrant sẵn sàng"
wait_for_url "Qdrant" "http://localhost:6333/healthz" 45

info "Chờ frontend sẵn sàng"
wait_for_url "Frontend" "http://localhost:5173" 60

export CONTRACT_REVIEW_RERANKER_ENABLED=true
export CONTRACT_REVIEW_RERANKER_MODEL="BAAI/bge-reranker-v2-m3"
export CONTRACT_REVIEW_RERANKER_CANDIDATE_K=15
export CONTRACT_REVIEW_RERANKER_BATCH_SIZE=1
export CONTRACT_REVIEW_RERANKER_MAX_LENGTH=512
export CONTRACT_REVIEW_RERANKER_DEVICE=cuda
export CONTRACT_REVIEW_RERANKER_NORMALIZE_SCORES=true
export CONTRACT_REVIEW_RERANKER_FAIL_OPEN=true

info "Kiểm tra CUDA và cấu hình Cross-Encoder"
PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/backend" \
  .venv/bin/python - <<'PY'
import torch
from app.services.contract_review_reranker import ContractReviewRerankerConfig

cfg = ContractReviewRerankerConfig.from_env()

print("CUDA available =", torch.cuda.is_available())
print("GPU            =", torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
print("enabled        =", cfg.enabled)
print("model          =", cfg.model_name)
print("candidate_k    =", cfg.candidate_k)
print("batch_size     =", cfg.batch_size)
print("max_length     =", cfg.max_length)
print("device         =", cfg.device)
print("fail_open      =", cfg.fail_open)

assert torch.cuda.is_available(), "CUDA không khả dụng"
assert cfg.enabled is True
assert cfg.model_name == "BAAI/bge-reranker-v2-m3"
assert cfg.candidate_k == 15
assert cfg.batch_size == 1
assert cfg.max_length == 512
assert cfg.device == "cuda"
assert cfg.fail_open is True

print("CROSS-ENCODER DEMO CONFIG PASS")
PY

info "Hệ thống hạ tầng đã sẵn sàng"
docker compose ps

cat <<'EOF'

Frontend: http://localhost:5173
Backend:  http://localhost:8000

Backend sẽ chạy ở foreground trong terminal này.
- Không đóng terminal khi đang demo.
- Nhấn Ctrl+C để dừng backend.
- Sau khi demo, chạy: ./scripts/start_demo_gpu.sh stop
  hoặc nếu đặt script ở thư mục gốc: ./start_demo_gpu.sh stop
- Thực hiện một lượt Contract Review trước để prewarm model/CUDA.
EOF

info "Khởi động backend host với Cross-Encoder K=15"
exec env PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/backend" \
  .venv/bin/python -m uvicorn \
  app.main:app \
  --host 0.0.0.0 \
  --port 8000
