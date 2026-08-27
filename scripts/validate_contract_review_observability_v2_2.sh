#!/usr/bin/env bash
# Contract Review V2.2 observability validation.
# Intentionally no `set -e`: every safe diagnostic is collected before final status.

REPO="${1:-$(pwd)}"
PY="$REPO/.venv/bin/python"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$REPO/data/evaluation/contract-review/observability-v2.2-validation-$TS"
STATUS="$OUT/step-status.tsv"
LOG="$OUT/validation.log"
mkdir -p "$OUT"
exec > >(tee -a "$LOG") 2>&1
printf 'step\tstatus\texit_code\n' > "$STATUS"
CRITICAL_FAILED=0

mark() {
  printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$STATUS"
}
run_step() {
  local name="$1"; shift
  echo
  echo "================================================================"
  echo "STEP: $name"
  echo "================================================================"
  "$@"
  local rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "[PASS] $name"; mark "$name" PASS 0
  else
    echo "[FAIL] $name (exit=$rc)"; mark "$name" FAIL "$rc"; CRITICAL_FAILED=1
  fi
  return "$rc"
}

if [ ! -x "$PY" ]; then
  echo "[FATAL] Missing project venv Python: $PY"
  mark venv_python FAIL 2
  exit 2
fi
cd "$REPO" || exit 2

export PYTHONPATH=.:backend

run_step patch_markers "$PY" - <<'PY'
from pathlib import Path
s = Path("backend/app/services/contract_review_service.py").read_text(encoding="utf-8")
r = Path("backend/app/services/contract_review_reranker.py").read_text(encoding="utf-8")
checks = {
    "V2 marker": "CONTRACT_REVIEW_CROSS_ENCODER_V2" in s,
    "V2.2 marker": "CONTRACT_REVIEW_CROSS_ENCODER_V2_OBSERVABILITY" in s,
    "scored rerank API": "rerank_with_scores" in r and "rerank_with_scores" in s,
    "finalizer": "_finalize_reranked_sources" in s,
    "cross-encoder origin": "contract_cross_encoder_v2" in s,
    "audit clean body": "audit_source_text" in Path("scripts/export_contract_review_source_relevance_audit_v2.py").read_text(encoding="utf-8"),
}
for k, ok in checks.items():
    print(f"{k}: {'OK' if ok else 'MISSING'}")
if not all(checks.values()):
    raise SystemExit(1)
PY

run_step python_compile "$PY" -m py_compile \
  backend/app/services/contract_review_service.py \
  backend/app/services/contract_review_reranker.py \
  backend/tests/test_contract_review_reranker.py \
  backend/tests/test_contract_review_reranker_observability.py \
  scripts/export_contract_review_source_relevance_audit_v2.py

run_step reranker_unit_tests env \
  CONTRACT_REVIEW_RERANKER_ENABLED=false \
  PYTHONPATH=.:backend \
  "$PY" -m pytest \
    backend/tests/test_contract_review_reranker.py \
    backend/tests/test_contract_review_reranker_observability.py -q

run_step contract_review_regression_v2_disabled env \
  CONTRACT_REVIEW_RERANKER_ENABLED=false \
  PYTHONPATH=.:backend \
  "$PY" -m pytest backend/tests/test_contract_reviews.py -q

run_step full_backend_regression_v2_disabled env \
  CONTRACT_REVIEW_RERANKER_ENABLED=false \
  PYTHONPATH=.:backend \
  "$PY" -m pytest backend/tests -q

run_step real_gpu_score_propagation_smoke env \
  CONTRACT_REVIEW_RERANKER_ENABLED=true \
  CONTRACT_REVIEW_RERANKER_MODEL=BAAI/bge-reranker-v2-m3 \
  CONTRACT_REVIEW_RERANKER_CANDIDATE_K=15 \
  CONTRACT_REVIEW_RERANKER_BATCH_SIZE=1 \
  CONTRACT_REVIEW_RERANKER_DEVICE=cuda \
  CONTRACT_REVIEW_RERANKER_FAIL_OPEN=false \
  PYTHONPATH=.:backend \
  "$PY" - <<'PY'
from app.services.contract_review_reranker import (
    get_contract_review_reranker,
    reset_contract_review_reranker_for_tests,
)
from app.services.contract_review_service import (
    CATEGORIES,
    _finalize_reranked_sources,
    evidence_retriever,
)

reset_contract_review_reranker_for_tests()
reranker = get_contract_review_reranker()
assert reranker.enabled
assert reranker.config.candidate_k == 15
assert reranker.config.batch_size == 1
assert reranker.config.device == "cuda"
assert reranker.config.fail_open is False

rule = next(x for x in CATEGORIES if x.key == "termination")
excerpt = "Người lao động làm theo hợp đồng xác định thời hạn 24 tháng muốn đơn phương chấm dứt và báo trước 30 ngày."
query = rule.query + " Nội dung hợp đồng: " + excerpt
candidates = evidence_retriever().retrieve(
    query,
    top_k=reranker.config.candidate_k,
    preferred_article_codes=rule.preferred_article_codes,
    dedupe_articles=False,
)
items = reranker.rerank_with_scores(
    query=excerpt,
    candidates=candidates,
    top_k=4,
)
sources = _finalize_reranked_sources(items)

assert len(sources) == 4
assert [s.source_id for s in sources] == ["S1", "S2", "S3", "S4"]
assert [s.rank for s in sources] == [1, 2, 3, 4]
assert all(item.reranker_score is not None for item in items)
assert all(s.retrieval_origin == "contract_cross_encoder_v2" for s in sources)
assert all(0.0 <= float(s.score) <= 1.0 for s in sources)
assert [float(s.score) for s in sources] == sorted(
    [float(s.score) for s in sources], reverse=True
)
assert all(s.component_ranks.get("contract_cross_encoder") == s.rank for s in sources)
assert "20.2.LQ.35" in {s.article_code for s in sources}

for s in sources:
    print(s.rank, s.article_code, s.score, s.retrieval_origin, s.component_ranks)
print("REAL GPU SCORE PROPAGATION PASS")
PY

run_step audit_text_smoke "$PY" - <<'PY'
import importlib.util
from pathlib import Path
p = Path("scripts/export_contract_review_source_relevance_audit_v2.py")
spec = importlib.util.spec_from_file_location("audit_v2", p)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
source = {
    "article_code": "20.2.LQ.110",
    "article_title": "Nghỉ chuyển ca",
    "content": "Điều 20.2.LQ.110 — Nghỉ chuyển ca\nNguồn: Văn bản hợp nhất\nNội dung.",
}
text = module.audit_source_text(source)
assert text == source["content"]
assert text.count("Điều 20.2.LQ.110") == 1
assert text.count("Nghỉ chuyển ca") == 1
print(text)
print("AUDIT TEXT PASS")
PY

# Existing DEV benchmark is read only; it is NOT rerun/tuned here.
BENCH="data/evaluation/contract-review/cross-encoder-v2-gpu-k15-dev-20260827/contract_review_cross_encoder_ablation.json"
if [ -f "$BENCH" ]; then
  run_step existing_k15_benchmark_readonly "$PY" - "$BENCH" <<'PY'
import json, sys
p = sys.argv[1]
x = json.load(open(p, encoding="utf-8"))
s = x["v2_cross_encoder"]["summary"]
print(json.dumps(s, indent=2, ensure_ascii=False))
assert s["any_hit_at_4"] == 1.0
assert s["macro_article_recall_at_4"] == 1.0
assert abs(s["article_mrr_at_4"] - 0.902778) < 1e-6
print("K15 BENCHMARK ARTIFACT CONSISTENT")
PY
else
  echo "[SKIP] Existing K15 benchmark artifact not found: $BENCH"
  mark existing_k15_benchmark_readonly SKIP 0
fi

sha256sum \
  backend/app/services/contract_review_service.py \
  backend/app/services/contract_review_reranker.py \
  backend/tests/test_contract_review_reranker_observability.py \
  scripts/export_contract_review_source_relevance_audit_v2.py \
  > "$OUT/SHA256SUMS"

if [ "$CRITICAL_FAILED" -eq 0 ]; then
  RESULT=PASS
else
  RESULT=FAIL
fi
cat > "$OUT/FINAL-STATUS.txt" <<EOF
Contract Review V2.2 observability validation
Result: $RESULT
Artifacts: $OUT

This validation does NOT rerun the consumed RAG TEST set and does NOT retune RAG.
The existing Contract Review 24-case DEV benchmark is read only when present.
EOF

echo
echo "===== FINAL STATUS ====="
cat "$OUT/FINAL-STATUS.txt"
echo "Step table: $STATUS"
echo "Log:        $LOG"

if [ "$CRITICAL_FAILED" -ne 0 ]; then
  exit 1
fi
exit 0
