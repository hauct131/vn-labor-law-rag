.PHONY: legal-eval-tune legal-eval-tune-fast

legal-eval-tune: golden-audit-unified
	PYTHONPATH=.:backend:scripts:$${PYTHONPATH} $(PYTHON) scripts/tune_retrieval_parameters.py \
	  --golden data/evaluation/golden_questions_v3_unified_candidate.json \
	  --chunks data/releases/labor-law-2026-07-28-candidate/chunks.jsonl \
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
	  --golden data/evaluation/golden_questions_v3_unified_candidate.json \
	  --chunks data/releases/labor-law-2026-07-28-candidate/chunks.jsonl \
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
