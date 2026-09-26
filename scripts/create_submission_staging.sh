#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(git rev-parse --show-toplevel)"
PROJECT="$(basename "$ROOT")"

STAGE_PARENT="${ROOT}/../submission-staging"
STAGE="${STAGE_PARENT}/${PROJECT}"

echo "============================================================"
echo "CREATE FINAL SUBMISSION STAGING"
echo "============================================================"
echo "SOURCE : $ROOT"
echo "STAGE  : $STAGE"
echo
echo "Repo gốc sẽ KHÔNG bị sửa."
echo

# Chỉ xóa staging cũ, tuyệt đối không xóa repo gốc.
rm -rf "$STAGE_PARENT"
mkdir -p "$STAGE"

echo "[1/6] Copy working tree..."

rsync -a \
  --exclude='.git' \
  --exclude='.git/' \
  --exclude='.venv/' \
  --exclude='venv/' \
  --exclude='node_modules/' \
  --exclude='__pycache__/' \
  --exclude='.pytest_cache/' \
  --exclude='.mypy_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='htmlcov/' \
  --exclude='coverage/' \
  --exclude='dist/' \
  --exclude='build/' \
  --include='.env.example' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='*.pyc' \
  --exclude='*.pyo' \
  --exclude='.DS_Store' \
  --exclude='Thumbs.db' \
  "$ROOT/" "$STAGE/"

echo "[2/6] Remove definite submission junk..."

rm -f "$STAGE/application.db"

rm -rf "$STAGE/_SOURCE_AUDIT"

rm -f \
  "$STAGE/contract_review_demo_regressions.patch" \
  "$STAGE/contract_review_fix_context.txt"

rm -f \
  "$STAGE/scripts/package_full_project_for_audit.sh"

rm -rf \
  "$STAGE/data/evaluation/answer-quality-v1/runtime-runs/4_3_full/run-20260901-164911"

rm -f \
  "$STAGE/FINAL_BUILD_REPORT_2026-07-27.md" \
  "$STAGE/FINAL_RELEASE_VERIFICATION_2026-08-10.md" \
  "$STAGE/FINAL_INTEGRATION_VERIFICATION_2026-08-17.md"

echo "[3/6] Remove generated leftovers missed by rsync..."

find "$STAGE" -type d \
  \( \
    -name '__pycache__' \
    -o -name '.pytest_cache' \
    -o -name '.mypy_cache' \
    -o -name '.ruff_cache' \
    -o -name 'node_modules' \
  \) \
  -prune -exec rm -rf {} +

find "$STAGE" -type f \
  \( -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' \) \
  -delete

echo "[4/6] Check forbidden sensitive/local files..."

FOUND_BAD=0

while IFS= read -r f; do
  echo "BAD FILE: $f"
  FOUND_BAD=1
done < <(
  find "$STAGE" -type f \
    \( \
      -name '.env' \
      -o -name '.env.before-*' \
      -o -name '*.pem' \
      -o -name '*.key' \
      -o -name '*.p12' \
      -o -name '*.pfx' \
      -o -name 'application.db' \
    \)
)

if [ "$FOUND_BAD" -ne 0 ]; then
  echo
  echo "FAIL: staging vẫn còn file nhạy cảm/local."
  exit 1
fi

echo "[5/6] Check expected important files..."

REQUIRED=(
  ".env.example"
  "README.md"
  "docker-compose.yml"
  "backend"
  "frontend"
  "scripts/start_demo_gpu.sh"
)

for item in "${REQUIRED[@]}"; do
  if [ ! -e "$STAGE/$item" ]; then
    echo "MISSING: $item"
    exit 1
  fi
done

echo "[6/6] Summary..."

echo
echo "Stage size:"
du -sh "$STAGE"

echo
echo "Top-level:"
find "$STAGE" -maxdepth 1 -mindepth 1 \
  -printf '%f\n' | sort

echo
echo "File count:"
find "$STAGE" -type f | wc -l

echo
echo "PASS: staging cơ bản đã được tạo."
echo
echo "STAGE=$STAGE"
echo
echo "CHƯA ZIP. Còn phải xử lý consistency/runtime trước."
