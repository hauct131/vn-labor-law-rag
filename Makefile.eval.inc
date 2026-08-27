# Retrieval and indexing targets for the locked canonical Word 804 candidate.

UNIFIED_RELEASE_DIR ?= data/releases/labor-law-canonical-word-20260804-164432-candidate
UNIFIED_CHUNKS ?= $(UNIFIED_RELEASE_DIR)/canonical_chunks.jsonl
UNIFIED_GOLDEN ?= data/evaluation/golden_questions_v4_canonical_word_candidate.json
UNIFIED_E5_AUDIT ?= $(UNIFIED_RELEASE_DIR)/e5_audit/summary.json
UNIFIED_EXPECTED_CHUNKS ?= 804
UNIFIED_EXPECTED_SHA256 ?= fdbec539efbfb3f4aa3cb3962046321e3a402150d93516ef4256934972c70307
UNIFIED_RESULTS_DIR ?= data/evaluation/results/canonical_word_804
CANONICAL_SPLIT_DIR ?= data/evaluation/splits/canonical_word_804
CANONICAL_LOCKED_RESULTS_DIR ?= data/evaluation/runtime-benchmark/canonical_word_804/locked
QDRANT_UNIFIED_COLLECTION ?= labor_law_canonical_word_20260804_fdbec539
QDRANT_ACTIVE_ALIAS ?= labor_law_active
E5_MODEL ?= intfloat/multilingual-e5-large
E5_DEVICE ?= cpu
E5_BATCH_SIZE ?= 16

.PHONY: golden-audit-unified legal-eval-bm25 \
	qdrant-index-unified qdrant-verify-unified \
	qdrant-activate-unified qdrant-verify-alias-unified \
	legal-eval-dense-hybrid legal-eval-dense-hybrid-diverse \
	legal-eval-compare legal-eval-tune legal-eval-tune-fast \
	runtime-golden-splits runtime-golden-splits-check \
	runtime-benchmark-dry-run runtime-benchmark-tune-dev \
	runtime-benchmark-test runtime-benchmark-locked-verify \
	final-release-verify unified-runtime

golden-audit-unified:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/audit_golden_dataset.py \
	  --golden $(UNIFIED_GOLDEN) \
	  --chunks $(UNIFIED_CHUNKS) \
	  --report data/evaluation/golden_v3_audit.json \
	  --strict

runtime-golden-splits:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/build_runtime_golden_splits.py \
	  --output-dir $(CANONICAL_SPLIT_DIR)

runtime-golden-splits-check:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/build_runtime_golden_splits.py \
	  --output-dir $(CANONICAL_SPLIT_DIR) \
	  --check

runtime-benchmark-dry-run: runtime-golden-splits-check
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/benchmark_runtime_retrieval.py \
	  --phase tune \
	  --split-role dev \
	  --golden $(CANONICAL_SPLIT_DIR)/golden_v3_dev.json \
	  --chunks $(UNIFIED_CHUNKS) \
	  --dry-run

runtime-benchmark-tune-dev:
	@echo "Refusing: canonical Word 804 test has been consumed; post-test tuning is locked."
	@false

runtime-benchmark-test:
	@echo "Refusing: locked test was already run once. Verify checked-in evidence instead."
	@false

runtime-benchmark-locked-verify: runtime-golden-splits-check
	cd $(CANONICAL_SPLIT_DIR) && sha256sum -c SHA256SUMS.txt
	cd $(CANONICAL_LOCKED_RESULTS_DIR) && sha256sum -c SHA256SUMS.txt

legal-eval-bm25: golden-audit-unified
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/evaluate_lexical_baseline.py \
	  --golden $(UNIFIED_GOLDEN) \
	  --chunks $(UNIFIED_CHUNKS) \
	  --output $(UNIFIED_RESULTS_DIR)/unified_bm25_baseline.json \
	  --k 5 10 20

qdrant-index-unified:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) -m backend.app.ingestion.index_qdrant \
	  --chunks $(UNIFIED_CHUNKS) \
	  --audit-summary $(UNIFIED_E5_AUDIT) \
	  --collection $(QDRANT_UNIFIED_COLLECTION) \
	  --expected-chunks $(UNIFIED_EXPECTED_CHUNKS) \
	  --expected-sha256 $(UNIFIED_EXPECTED_SHA256) \
	  --dense-model intfloat/multilingual-e5-large \
	  --dense-vector-name dense \
	  --dense-size 1024 \
	  --sparse-model Qdrant/bm25 \
	  --sparse-vector-name sparse \
	  --resume \
	  --summary-output data/processed/qdrant_index/unified_summary.json

qdrant-verify-unified:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) -m backend.app.ingestion.index_qdrant \
	  --chunks $(UNIFIED_CHUNKS) \
	  --audit-summary $(UNIFIED_E5_AUDIT) \
	  --collection $(QDRANT_UNIFIED_COLLECTION) \
	  --expected-chunks $(UNIFIED_EXPECTED_CHUNKS) \
	  --expected-sha256 $(UNIFIED_EXPECTED_SHA256) \
	  --dense-model intfloat/multilingual-e5-large \
	  --dense-vector-name dense \
	  --dense-size 1024 \
	  --sparse-model Qdrant/bm25 \
	  --sparse-vector-name sparse \
	  --verify-only \
	  --summary-output data/processed/qdrant_index/unified_verify.json

qdrant-activate-unified:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) -m backend.app.ingestion.qdrant_alias \
	  --collection $(QDRANT_UNIFIED_COLLECTION) \
	  --alias $(QDRANT_ACTIVE_ALIAS) \
	  --expected-chunks $(UNIFIED_EXPECTED_CHUNKS) \
	  --expected-sha256 $(UNIFIED_EXPECTED_SHA256) \
	  --summary-output data/processed/qdrant_index/unified_alias.json

qdrant-verify-alias-unified:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) -m backend.app.ingestion.qdrant_alias \
	  --collection $(QDRANT_UNIFIED_COLLECTION) \
	  --alias $(QDRANT_ACTIVE_ALIAS) \
	  --expected-chunks $(UNIFIED_EXPECTED_CHUNKS) \
	  --expected-sha256 $(UNIFIED_EXPECTED_SHA256) \
	  --verify-only \
	  --summary-output data/processed/qdrant_index/unified_alias_verify.json

legal-eval-dense-hybrid: golden-audit-unified
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/evaluate_dense_hybrid.py \
	  --golden $(UNIFIED_GOLDEN) \
	  --chunks $(UNIFIED_CHUNKS) \
	  --output-dir $(UNIFIED_RESULTS_DIR) \
	  --output-prefix unified \
	  --model $(E5_MODEL) \
	  --device $(E5_DEVICE) \
	  --batch-size $(E5_BATCH_SIZE) \
	  --modes dense hybrid \
	  --candidate-k 50 \
	  --k 5 10 20

legal-eval-dense-hybrid-diverse: golden-audit-unified
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/evaluate_dense_hybrid.py \
	  --golden $(UNIFIED_GOLDEN) \
	  --chunks $(UNIFIED_CHUNKS) \
	  --output-dir $(UNIFIED_RESULTS_DIR) \
	  --output-prefix unified \
	  --model $(E5_MODEL) \
	  --device $(E5_DEVICE) \
	  --batch-size $(E5_BATCH_SIZE) \
	  --modes dense hybrid \
	  --candidate-k 50 \
	  --max-chunks-per-article 1 \
	  --drop-missing-article-code \
	  --k 5 10 20

legal-eval-compare:
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/compare_retrieval_reports.py \
	  --k 10 \
	  $(UNIFIED_RESULTS_DIR)/unified_bm25_baseline.json \
	  $(UNIFIED_RESULTS_DIR)/unified_dense.json \
	  $(UNIFIED_RESULTS_DIR)/unified_hybrid.json

legal-eval-tune: golden-audit-unified
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/tune_retrieval_parameters.py \
	  --golden $(UNIFIED_GOLDEN) \
	  --chunks $(UNIFIED_CHUNKS) \
	  --output-dir data/evaluation/tuning-unified \
	  --embedding-cache-dir data/evaluation/cache/embeddings \
	  --model $(E5_MODEL) \
	  --device $(E5_DEVICE) \
	  --batch-size $(E5_BATCH_SIZE) \
	  --candidate-k 20 50 \
	  --article-cap 0 1 2 \
	  --dense-weight 0.6 0.7 0.8 0.9 \
	  --rrf-constant 30 60 \
	  --selected-articles 8 10 12 \
	  --chunks-per-article 1 2 3 \
	  --k 5 10 20

legal-eval-tune-fast: golden-audit-unified
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/tune_retrieval_parameters.py \
	  --golden $(UNIFIED_GOLDEN) \
	  --chunks $(UNIFIED_CHUNKS) \
	  --output-dir data/evaluation/tuning-unified-fast \
	  --embedding-cache-dir data/evaluation/cache/embeddings \
	  --model $(E5_MODEL) \
	  --device $(E5_DEVICE) \
	  --batch-size $(E5_BATCH_SIZE) \
	  --candidate-k 50 \
	  --article-cap 0 1 \
	  --dense-weight 0.7 0.8 0.9 \
	  --rrf-constant 60 \
	  --selected-articles 8 10 \
	  --chunks-per-article 1 2 3 \
	  --k 5 10 20

final-release-verify:
	bash scripts/verify_final_release.sh

unified-runtime: final-release-verify
