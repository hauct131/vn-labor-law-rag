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
