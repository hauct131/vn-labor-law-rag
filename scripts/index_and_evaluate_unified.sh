#!/usr/bin/env bash
#
# Legacy validator/indexer for the retired unified 833-chunk candidate.
#
# The active release is canonical Word 804. Use verify_final_release.sh.
#
# Safety properties:
#   - resolves the repository relative to this file;
#   - invokes .venv/bin/python3 directly, never an ambient `python`;
#   - preserves the legacy labor_law collection for rollback;
#   - indexes into a deterministic versioned physical collection;
#   - resumes only when that versioned collection's schema/fingerprint match;
#   - activates labor_law_active only after verify and retrieval smoke checks;
#   - writes a timestamped log and keeps an interactive terminal open on error.

set +e

if [[ "${ALLOW_LEGACY_UNIFIED_833:-0}" != "1" ]]; then
  echo "REFUSED: scripts/index_and_evaluate_unified.sh targets the retired 833-chunk candidate."
  echo "Run: bash scripts/verify_final_release.sh"
  echo "Set ALLOW_LEGACY_UNIFIED_833=1 only for explicit historical reproduction."
  exit 2
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
RELEASE_REL="data/releases/labor-law-2026-07-28-candidate"
CHUNKS_REL="${RELEASE_REL}/chunks.jsonl"
AUDIT_REL="data/quality/unified_e5_token_audit/summary.json"
GOLDEN_REL="data/evaluation/golden_questions_v3_unified_candidate.json"
LEGACY_COLLECTION="${QDRANT_LEGACY_COLLECTION:-labor_law}"
INDEX_COLLECTION="${QDRANT_INDEX_COLLECTION:-labor_law_20260728_fd35bb1a}"
ACTIVE_ALIAS="${QDRANT_ACTIVE_ALIAS:-labor_law_active}"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
EXPECTED_CHUNKS="833"
EXPECTED_SHA256="fd35bb1a94a3036f7977781de17bb1b49b12c58be61fc74efac68dcf8a7a8c54"
RUN_DENSE_HYBRID="${RUN_DENSE_HYBRID:-auto}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="${REPO}/logs"
LOG_FILE="${LOG_DIR}/unified_runtime_${STAMP}.log"
INDEX_SUMMARY="${LOG_DIR}/qdrant_index_unified_${STAMP}.json"
VERIFY_SUMMARY="${LOG_DIR}/qdrant_verify_unified_${STAMP}.json"
ALIAS_SUMMARY="${LOG_DIR}/qdrant_alias_activate_${STAMP}.json"
ALIAS_VERIFY_SUMMARY="${LOG_DIR}/qdrant_alias_verify_${STAMP}.json"
PHYSICAL_SMOKE_REPORT="${LOG_DIR}/qdrant_physical_dense_smoke_${STAMP}.json"
ALIAS_SMOKE_REPORT="${LOG_DIR}/qdrant_alias_dense_smoke_${STAMP}.json"
ACTIVATE_ALIAS="${ACTIVATE_ALIAS:-0}"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_EXE="${PYTHON_BIN}"
elif [[ -x "${REPO}/.venv/bin/python3" ]]; then
  PYTHON_EXE="${REPO}/.venv/bin/python3"
elif [[ -x "${REPO}/.venv/bin/python" ]]; then
  PYTHON_EXE="${REPO}/.venv/bin/python"
else
  PYTHON_EXE=""
fi

mkdir -p "$LOG_DIR"

run_all() (
  set -Eeuo pipefail
  trap 'printf "\nFAILED at line %s: %s\n" "$LINENO" "$BASH_COMMAND"' ERR

  cd "$REPO"

  echo "Script version: 2026-07-28-unified-runtime-v3-bluegreen"
  echo "Repo: $REPO"
  echo "Release: $RELEASE_REL"
  echo "Legacy collection (preserved): $LEGACY_COLLECTION"
  echo "Versioned collection: $INDEX_COLLECTION"
  echo "Active alias: $ACTIVE_ALIAS"
  echo "Qdrant: $QDRANT_URL"
  echo "Log: $LOG_FILE"
  echo "Dense/hybrid mode: $RUN_DENSE_HYBRID"

  if [[ "$INDEX_COLLECTION" == "$LEGACY_COLLECTION" ]]; then
    echo "ERROR: Versioned collection must differ from legacy collection."
    return 2
  fi
  if [[ "$ACTIVE_ALIAS" == "$LEGACY_COLLECTION" ]]; then
    echo "ERROR: Active alias must not collide with legacy collection."
    return 2
  fi
  if [[ "$ACTIVE_ALIAS" == "$INDEX_COLLECTION" ]]; then
    echo "ERROR: Active alias must differ from versioned collection."
    return 2
  fi
  if [[ -n "${QDRANT_COLLECTION:-}" \
        && "$QDRANT_COLLECTION" != "$ACTIVE_ALIAS" ]]; then
    echo "WARNING: Exported QDRANT_COLLECTION=$QDRANT_COLLECTION"
    echo "Backend must use active alias: $ACTIVE_ALIAS"
  fi

  echo
  echo "===== PYTHON ENVIRONMENT ====="
  if [[ -z "$PYTHON_EXE" || ! -x "$PYTHON_EXE" ]]; then
    echo "ERROR: Không tìm thấy Python trong .venv."
    echo "Tạo/cài môi trường trước bằng Python 3.12 và backend/requirements.txt."
    return 2
  fi
  echo "Python executable: $PYTHON_EXE"
  "$PYTHON_EXE" --version
  "$PYTHON_EXE" - <<'PY'
import fastembed
import pydantic_settings
import qdrant_client

print("fastembed=AVAILABLE")
print("pydantic_settings=AVAILABLE")
print("qdrant_client=AVAILABLE")
PY

  echo
  echo "===== RELEASE PREFLIGHT ====="
  "$PYTHON_EXE" - \
    "$CHUNKS_REL" \
    "$AUDIT_REL" \
    "${RELEASE_REL}/manifest.json" \
    "$EXPECTED_CHUNKS" \
    "$EXPECTED_SHA256" <<'PY'
import hashlib
import json
import sys
import uuid
from pathlib import Path

chunks_path, audit_path, manifest_path, expected_count_text, expected_sha = (
    sys.argv[1:]
)
expected_count = int(expected_count_text)
chunks_file = Path(chunks_path)
audit_file = Path(audit_path)
manifest_file = Path(manifest_path)

for path in (chunks_file, audit_file, manifest_file):
    if not path.is_file():
        raise SystemExit(f"Missing required file: {path}")

rows = []
seen = set()
with chunks_file.open(encoding="utf-8") as source:
    for line_no, line in enumerate(source, 1):
        if not line.strip():
            continue
        node = json.loads(line)
        chunk_id = node.get("chunk_id")
        uuid.UUID(chunk_id)
        if chunk_id in seen:
            raise SystemExit(f"Duplicate chunk_id at line {line_no}: {chunk_id}")
        if not str(node.get("content", "")).strip():
            raise SystemExit(f"Empty content at line {line_no}: {chunk_id}")
        seen.add(chunk_id)
        rows.append(node)

actual_sha = hashlib.sha256(chunks_file.read_bytes()).hexdigest()
manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
audit = json.loads(audit_file.read_text(encoding="utf-8"))

checks = {
    "chunk_count": len(rows) == expected_count,
    "chunks_sha256": actual_sha == expected_sha,
    "manifest_count": manifest["counts"]["chunks"] == expected_count,
    "manifest_sha256": manifest["hashes"]["chunks_sha256"] == expected_sha,
    "audit_count": audit["chunk_count"] == expected_count,
    "audit_sha256": audit["input_sha256"] == expected_sha,
    "audit_model": audit["model_name"] == "intfloat/multilingual-e5-large",
    "audit_exact": audit["exact_measurement"] is True,
    "audit_over_limit": (
        audit.get("risk_counts", {}).get(
            "strictly_over_model_limit_count", 0
        )
        == 0
    ),
    "audit_near_limit": (
        audit.get("risk_counts", {}).get("near_or_above_count", 0) == 0
    ),
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit("Release preflight failed: " + ", ".join(failed))

print(f"chunks={len(rows)}")
print(f"chunks_sha256={actual_sha}")
print("audit_exact_measurement=true")
print("RELEASE_PREFLIGHT_PASSED")
PY

  (
    cd "$RELEASE_REL"
    sha256sum -c SHA256SUMS.txt
  )

  echo
  echo "===== UNIFIED RELEASE VALIDATOR ====="
  PYTHONPATH=".:backend:${PYTHONPATH:-}" \
    "$PYTHON_EXE" scripts/validate_unified_release.py \
      --release-dir "$RELEASE_REL"

  echo
  echo "===== GOLDEN V3 AUDIT ====="
  PYTHONPATH=".:backend:${PYTHONPATH:-}" \
    "$PYTHON_EXE" scripts/audit_golden_dataset.py \
      --golden "$GOLDEN_REL" \
      --chunks "$CHUNKS_REL" \
      --report data/evaluation/golden_v3_audit.json \
      --strict

  echo
  echo "===== BM25 BASELINE ====="
  PYTHONPATH=".:backend:scripts:${PYTHONPATH:-}" \
    "$PYTHON_EXE" scripts/evaluate_lexical_baseline.py \
      --golden "$GOLDEN_REL" \
      --chunks "$CHUNKS_REL" \
      --output data/evaluation/results/unified_bm25_baseline.json \
      --k 5 10 20

  echo
  echo "===== START QDRANT ====="
  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: Không tìm thấy lệnh docker."
    return 3
  fi
  docker compose version
  docker compose up -d qdrant
  docker compose ps qdrant

  "$PYTHON_EXE" - "$QDRANT_URL" <<'PY'
import sys
import time
import urllib.error
import urllib.request

url = sys.argv[1].rstrip("/") + "/healthz"
last_error = None
for _ in range(60):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if 200 <= response.status < 300:
                print(f"QDRANT_HEALTHY status={response.status}")
                raise SystemExit(0)
    except (OSError, urllib.error.URLError) as exc:
        last_error = exc
    time.sleep(1)
raise SystemExit(f"Qdrant not healthy after 60 seconds: {last_error}")
PY

  common_args=(
    --chunks "$CHUNKS_REL"
    --audit-summary "$AUDIT_REL"
    --qdrant-url "$QDRANT_URL"
    --collection "$INDEX_COLLECTION"
    --expected-chunks "$EXPECTED_CHUNKS"
    --expected-sha256 "$EXPECTED_SHA256"
    --dense-model intfloat/multilingual-e5-large
    --dense-vector-name dense
    --dense-size 1024
    --sparse-model Qdrant/bm25
    --sparse-vector-name sparse
  )

  echo
  echo "===== INDEXER DRY RUN ====="
  PYTHONPATH=".:backend:${PYTHONPATH:-}" \
    "$PYTHON_EXE" -m backend.app.ingestion.index_qdrant \
      "${common_args[@]}" \
      --dry-run

  echo
  echo "===== INDEX VERSIONED COLLECTION OR SAFE RESUME ====="
  echo "Collection cũ '$LEGACY_COLLECTION' được giữ nguyên để rollback."
  PYTHONPATH=".:backend:${PYTHONPATH:-}" \
    "$PYTHON_EXE" -m backend.app.ingestion.index_qdrant \
      "${common_args[@]}" \
      --resume \
      --summary-output "$INDEX_SUMMARY"

  echo
  echo "===== VERIFY VERSIONED COLLECTION ====="
  PYTHONPATH=".:backend:${PYTHONPATH:-}" \
    "$PYTHON_EXE" -m backend.app.ingestion.index_qdrant \
      "${common_args[@]}" \
      --verify-only \
      --summary-output "$VERIFY_SUMMARY"

  "$PYTHON_EXE" - "$VERIFY_SUMMARY" "$EXPECTED_CHUNKS" "$EXPECTED_SHA256" <<'PY'
import json
import sys
from pathlib import Path

path, count_text, expected_sha = sys.argv[1:]
report = json.loads(Path(path).read_text(encoding="utf-8"))
checks = {
    "status": report.get("status") == "verified",
    "chunk_count": report.get("chunk_count") == int(count_text),
    "exact_point_count": report.get("exact_point_count") == int(count_text),
    "corpus_sha256": report.get("corpus_sha256") == expected_sha,
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit("Qdrant verify summary failed: " + ", ".join(failed))
print("QDRANT_RELEASE_833_INDEXED_AND_VERIFIED")
PY

  echo
  echo "===== VERSIONED COLLECTION DENSE SMOKE ====="
  PYTHONPATH=".:backend:scripts:${PYTHONPATH:-}" \
    "$PYTHON_EXE" scripts/evaluate_retrieval_core.py \
      --methods dense \
      --qdrant-url "$QDRANT_URL" \
      --collection "$INDEX_COLLECTION" \
      --output "$PHYSICAL_SMOKE_REPORT"

  "$PYTHON_EXE" - \
    "$PHYSICAL_SMOKE_REPORT" \
    "$INDEX_COLLECTION" \
    "$EXPECTED_SHA256" <<'PY'
import json
import sys
from pathlib import Path

path, collection, expected_sha = sys.argv[1:]
report = json.loads(Path(path).read_text(encoding="utf-8"))
questions = report.get("questions") or []
checks = {
    "status": report.get("status") == "completed",
    "collection": report.get("config", {}).get("collection") == collection,
    "corpus_sha256": (
        report.get("config", {}).get("corpus_sha256") == expected_sha
    ),
    "questions_present": len(questions) > 0,
    "dense_results_present": all(
        len(question.get("methods", {}).get("dense", {}).get("results", []))
        > 0
        for question in questions
    ),
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit(
        "Versioned collection dense smoke failed: " + ", ".join(failed)
    )
print("VERSIONED_COLLECTION_DENSE_SMOKE_PASSED")
PY

  echo
  echo "===== FULL GOLDEN DENSE/HYBRID EVALUATION ====="
  run_offline=0
  case "$RUN_DENSE_HYBRID" in
    1|true|yes)
      run_offline=1
      ;;
    0|false|no)
      run_offline=0
      ;;
    auto)
      if "$PYTHON_EXE" -c 'import sentence_transformers' >/dev/null 2>&1; then
        run_offline=1
      fi
      ;;
    *)
      echo "ERROR: RUN_DENSE_HYBRID must be auto, 1, or 0."
      return 4
      ;;
  esac

  if [[ "$run_offline" -eq 1 ]]; then
    make legal-eval-dense-hybrid \
      PYTHON="$PYTHON_EXE" \
      E5_MODEL=intfloat/multilingual-e5-large \
      E5_DEVICE="${E5_DEVICE:-cpu}" \
      E5_BATCH_SIZE="${E5_BATCH_SIZE:-16}"
    make legal-eval-compare PYTHON="$PYTHON_EXE"
    echo "DENSE_HYBRID_GOLDEN_COMPLETED"
  else
    echo "DENSE_HYBRID_GOLDEN_SKIPPED"
    echo "Lý do: sentence-transformers chưa có hoặc RUN_DENSE_HYBRID=0."
    echo "Cài requirements-evaluation.txt rồi chạy: make legal-eval-dense-hybrid"
  fi
if [[ "$ACTIVATE_ALIAS" == "1" ]]; then
  echo
  echo "===== ACTIVATE BLUE/GREEN ALIAS ====="
  echo "Chỉ đổi alias; không xóa hoặc sửa collection '$LEGACY_COLLECTION'."
  PYTHONPATH=".:backend:${PYTHONPATH:-}" \
    "$PYTHON_EXE" -m backend.app.ingestion.qdrant_alias \
      --qdrant-url "$QDRANT_URL" \
      --collection "$INDEX_COLLECTION" \
      --alias "$ACTIVE_ALIAS" \
      --expected-chunks "$EXPECTED_CHUNKS" \
      --expected-sha256 "$EXPECTED_SHA256" \
      --summary-output "$ALIAS_SUMMARY"

  echo
  echo "===== VERIFY ACTIVE ALIAS ====="
  PYTHONPATH=".:backend:${PYTHONPATH:-}" \
    "$PYTHON_EXE" -m backend.app.ingestion.qdrant_alias \
      --qdrant-url "$QDRANT_URL" \
      --collection "$INDEX_COLLECTION" \
      --alias "$ACTIVE_ALIAS" \
      --expected-chunks "$EXPECTED_CHUNKS" \
      --expected-sha256 "$EXPECTED_SHA256" \
      --verify-only \
      --summary-output "$ALIAS_VERIFY_SUMMARY"

  echo
  echo "===== ACTIVE ALIAS DENSE SMOKE ====="
  PYTHONPATH=".:backend:scripts:${PYTHONPATH:-}" \
    "$PYTHON_EXE" scripts/evaluate_retrieval_core.py \
      --methods dense \
      --qdrant-url "$QDRANT_URL" \
      --collection "$ACTIVE_ALIAS" \
      --output "$ALIAS_SMOKE_REPORT"

  "$PYTHON_EXE" - \
    "$ALIAS_SMOKE_REPORT" \
    "$ACTIVE_ALIAS" \
    "$EXPECTED_SHA256" <<'PY'
import json
import sys
from pathlib import Path

path, alias, expected_sha = sys.argv[1:]
report = json.loads(Path(path).read_text(encoding="utf-8"))
questions = report.get("questions") or []
checks = {
    "status": report.get("status") == "completed",
    "alias": report.get("config", {}).get("collection") == alias,
    "corpus_sha256": (
        report.get("config", {}).get("corpus_sha256") == expected_sha
    ),
    "questions_present": len(questions) > 0,
    "dense_results_present": all(
        len(question.get("methods", {}).get("dense", {}).get("results", []))
        > 0
        for question in questions
    ),
}
failed = [name for name, passed in checks.items() if not passed]
if failed:
    raise SystemExit(
        "Active alias dense smoke failed: " + ", ".join(failed)
    )
print("ACTIVE_ALIAS_DENSE_SMOKE_PASSED")
PY

  echo
  echo "===== BIND LOCAL BACKEND ENV TO ACTIVE ALIAS ====="
  "$PYTHON_EXE" - "$ACTIVE_ALIAS" "$STAMP" <<'PY'
import re
import shutil
import stat
import sys
from pathlib import Path

alias, stamp = sys.argv[1:]
env_path = Path(".env")
if not env_path.is_file():
    print("No .env file found; backend code default already uses active alias.")
    raise SystemExit(0)

backup = env_path.with_name(f".env.pre-qdrant-alias-{stamp}")
shutil.copy2(env_path, backup)
original_mode = stat.S_IMODE(env_path.stat().st_mode)
lines = env_path.read_text(encoding="utf-8").splitlines()
pattern = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?)QDRANT_COLLECTION\s*=.*$",
    re.IGNORECASE,
)
updated = []
replaced = False
for line in lines:
    match = pattern.match(line)
    if not match:
        updated.append(line)
        continue
    if not replaced:
        updated.append(
            f"{match.group('prefix')}QDRANT_COLLECTION={alias}"
        )
        replaced = True
if not replaced:
    if updated and updated[-1] != "":
        updated.append("")
    updated.append(f"QDRANT_COLLECTION={alias}")
env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
env_path.chmod(original_mode)
print(f"Updated .env QDRANT_COLLECTION={alias}")
print(f"Backup: {backup}")
PY
  else
    echo
    echo "===== STAGING ONLY ====="
    echo "ACTIVATE_ALIAS=0: không đổi alias '$ACTIVE_ALIAS'."
    echo "Không cập nhật QDRANT_COLLECTION trong .env."
  fi
  echo
  echo "UNIFIED_CORE_WORKFLOW_COMPLETED"
  echo "Index summary: $INDEX_SUMMARY"
  echo "Verify summary: $VERIFY_SUMMARY"
  echo "Alias summary: $ALIAS_SUMMARY"
  echo "Alias verify summary: $ALIAS_VERIFY_SUMMARY"
  echo "Legacy rollback collection: $LEGACY_COLLECTION"
  echo "Active alias: $ACTIVE_ALIAS"
  if [[ "$ACTIVATE_ALIAS" == "1" ]]; then
    echo "QDRANT_BLUE_GREEN_833_ACTIVE"
  else
    echo "QDRANT_STAGING_833_READY_NOT_ACTIVATED"
  fi
)

run_all 2>&1 | tee "$LOG_FILE"
script_rc=${PIPESTATUS[0]}

echo
if [[ "$script_rc" -eq 0 ]]; then
  echo "SCRIPT_COMPLETED"
else
  echo "SCRIPT_STOPPED_SAFELY exit_code=$script_rc"
  echo "Không có lệnh xóa collection trong workflow này."
  echo "Collection cũ '$LEGACY_COLLECTION' vẫn được giữ nguyên."
fi
echo "Log: $LOG_FILE"

if [[ -t 0 ]]; then
  read -r -p "Nhấn Enter để kết thúc script..." _
fi

# Preserve the optional interactive pause, but always propagate the real result.
exit "$script_rc"
