# Retrieval benchmark targets.
# Add `include Makefile.eval.inc` at the end of the root Makefile.

.PHONY: golden-audit-rebased legal-eval-bm25

golden-audit-rebased:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/audit_golden_dataset.py \
	  --golden data/evaluation/golden_questions_v1_rebased_current_phapdien.json \
	  --chunks data/processed/legal_chunks.jsonl \
	  --report data/evaluation/golden_v1_rebased_audit.json \
	  --strict

legal-eval-bm25: golden-audit-rebased
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/evaluate_lexical_baseline.py \
	  --golden data/evaluation/golden_questions_v1_rebased_current_phapdien.json \
	  --chunks data/processed/legal_chunks.jsonl \
	  --output data/evaluation/results/phapdien_bm25_baseline.json \
	  --k 5 10 20
# Add this content to Makefile.eval.inc.

E5_MODEL ?= intfloat/multilingual-e5-small
E5_DEVICE ?= cpu
E5_BATCH_SIZE ?= 32

.PHONY: legal-eval-dense-hybrid legal-eval-dense-hybrid-diverse legal-eval-compare

legal-eval-dense-hybrid: golden-audit-rebased
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/evaluate_dense_hybrid.py \
	  --golden data/evaluation/golden_questions_v1_rebased_current_phapdien.json \
	  --chunks data/processed/legal_chunks.jsonl \
	  --output-dir data/evaluation/results \
	  --model $(E5_MODEL) \
	  --device $(E5_DEVICE) \
	  --batch-size $(E5_BATCH_SIZE) \
	  --modes dense hybrid \
	  --candidate-k 50 \
	  --k 5 10 20

legal-eval-dense-hybrid-diverse: golden-audit-rebased
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/evaluate_dense_hybrid.py \
	  --golden data/evaluation/golden_questions_v1_rebased_current_phapdien.json \
	  --chunks data/processed/legal_chunks.jsonl \
	  --output-dir data/evaluation/results \
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
	  data/evaluation/results/phapdien_bm25_baseline.json \
	  data/evaluation/results/phapdien_dense.json \
	  data/evaluation/results/phapdien_hybrid.json
.PHONY: legal-eval-tune legal-eval-tune-fast

legal-eval-tune: golden-audit-rebased
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/tune_retrieval_parameters.py \
	  --golden data/evaluation/golden_questions_v1_rebased_current_phapdien.json \
	  --chunks data/processed/legal_chunks.jsonl \
	  --output-dir data/evaluation/tuning \
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

legal-eval-tune-fast: golden-audit-rebased
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/tune_retrieval_parameters.py \
	  --golden data/evaluation/golden_questions_v1_rebased_current_phapdien.json \
	  --chunks data/processed/legal_chunks.jsonl \
	  --output-dir data/evaluation/tuning-fast \
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
