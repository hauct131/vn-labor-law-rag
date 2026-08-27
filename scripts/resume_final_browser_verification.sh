#!/usr/bin/env bash
# Resume only the failed real-browser gate from a complete prior verification.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREVIOUS_REPORT="${1:-}"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARTIFACT_DIR="${2:-$HOME/Downloads/vn-labor-law-rag-browser-resume-$STAMP}"
REPORT="$ARTIFACT_DIR/final-verification-browser-resume.txt"
PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python3}"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)

if [[ -z "$PREVIOUS_REPORT" ]]; then
  echo "Usage: $0 PREVIOUS_FINAL_VERIFICATION_REPORT [ARTIFACT_DIR]"
  exit 2
fi

mkdir -p "$ARTIFACT_DIR"
exec > >(tee "$REPORT") 2>&1
on_error() {
  local code=$?
  echo "FINAL VERIFICATION: FAIL (browser resume, exit=$code, line=$LINENO)"
  echo "Report: $REPORT"
  exit "$code"
}
trap on_error ERR

cd "$ROOT"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "ERROR: project Python not found: $PYTHON_BIN"
  exit 2
fi
if [[ ! -f "$PREVIOUS_REPORT" ]]; then
  echo "ERROR: previous verification report not found: $PREVIOUS_REPORT"
  exit 2
fi

required_markers=(
  "PASS: clean tracked tree, no merge markers, no tracked secrets"
  "635 passed, 3 skipped"
  "found 0 vulnerabilities"
  '"schema_version": "contract-review-docker-e2e-v1"'
  "RUNTIME SMOKE: PASS"
  '"status":"ready"'
  "===== 6. REAL BROWSER E2E ====="
  "FINAL VERIFICATION: FAIL"
)
for marker in "${required_markers[@]}"; do
  if ! grep -Fq "$marker" "$PREVIOUS_REPORT"; then
    echo "ERROR: previous report is missing required evidence: $marker"
    exit 3
  fi
done

previous_commit="$(sed -n 's/^Commit: //p' "$PREVIOUS_REPORT" | head -n 1)"
if [[ ! "$previous_commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo "ERROR: previous report does not contain a valid commit"
  exit 3
fi
git cat-file -e "$previous_commit^{commit}"
git merge-base --is-ancestor "$previous_commit" HEAD

while IFS= read -r changed_path; do
  case "$changed_path" in
    backend/tests/test_final_release_bindings.py | \
    scripts/resume_final_browser_verification.sh | \
    scripts/smoke_contract_review_browser.py | \
    scripts/verify_final_release.sh)
      ;;
    *)
      echo "ERROR: product file changed after prior verification: $changed_path"
      echo "Run the full verifier instead of browser resume."
      exit 3
      ;;
  esac
done < <(git diff --name-only "$previous_commit"..HEAD)

git diff --check
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "ERROR: tracked working tree is not clean"
  git status --short
  exit 3
fi

echo "PASS: prior non-browser gates are valid for unchanged product code"

"${COMPOSE[@]}" up -d qdrant postgres
"${COMPOSE[@]}" exec -T postgres sh -lc \
  'psql -v ON_ERROR_STOP=1 \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    --set="db_user=$POSTGRES_USER" \
    --set="db_password=$POSTGRES_PASSWORD"' <<'SQL'
ALTER ROLE :"db_user" WITH PASSWORD :'db_password';
SQL
"${COMPOSE[@]}" up -d backend frontend

curl --fail --silent http://127.0.0.1:8000/api/live >/dev/null
curl --fail --silent http://127.0.0.1:8000/api/ready \
  | tee "$ARTIFACT_DIR/runtime-ready.json"
echo

"$PYTHON_BIN" -c 'import playwright'
"$PYTHON_BIN" scripts/smoke_contract_review_browser.py \
  --frontend-url http://localhost:5173 \
  --output "$ARTIFACT_DIR/contract-review-browser-e2e.json" \
  --screenshot "$ARTIFACT_DIR/contract-review-browser-final.png"

git diff --check
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "ERROR: tracked working tree changed during browser verification"
  git status --short
  exit 3
fi

echo
echo "FINAL VERIFICATION: PASS"
echo "Mode: resumed real-browser gate from verified prior non-browser gates"
echo "Previous report: $PREVIOUS_REPORT"
echo "Authority review remains pending."
echo "Report: $REPORT"
