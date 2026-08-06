# Add this content to Makefile.eval.inc.

E5_MODEL ?= intfloat/multilingual-e5-large
E5_DEVICE ?= cpu
E5_BATCH_SIZE ?= 32

.PHONY: legal-eval-dense-hybrid legal-eval-dense-hybrid-diverse legal-eval-compare

legal-eval-dense-hybrid: golden-audit-unified
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/evaluate_dense_hybrid.py \
	  --golden data/evaluation/golden_questions_v3_unified_candidate.json \
	  --chunks data/releases/labor-law-2026-07-28-candidate/chunks.jsonl \
	  --output-dir data/evaluation/results \
	  --output-prefix unified \
	  --model $(E5_MODEL) \
	  --device $(E5_DEVICE) \
	  --batch-size $(E5_BATCH_SIZE) \
	  --modes dense hybrid \
	  --candidate-k 50 \
	  --k 5 10 20

legal-eval-dense-hybrid-diverse: golden-audit-unified
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/evaluate_dense_hybrid.py \
	  --golden data/evaluation/golden_questions_v3_unified_candidate.json \
	  --chunks data/releases/labor-law-2026-07-28-candidate/chunks.jsonl \
	  --output-dir data/evaluation/results \
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
	  data/evaluation/results/unified_bm25_baseline.json \
	  data/evaluation/results/unified_dense.json \
	  data/evaluation/results/unified_hybrid.json
