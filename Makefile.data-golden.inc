# Add these targets to your root Makefile, or include this file manually.

.PHONY: data-manifests golden-audit-legacy golden-rebase-phapdien golden-audit-rebased

data-manifests:
	PYTHONPATH=.:backend $(PYTHON) scripts/build_data_manifests.py

golden-audit-legacy:
	PYTHONPATH=.:backend $(PYTHON) scripts/audit_golden_dataset.py \
		--golden data/evaluation/golden_questions_v1_legacy.json \
		--chunks data/processed/legal_chunks.jsonl \
		--report data/evaluation/golden_v1_legacy_audit.json

golden-rebase-phapdien:
	PYTHONPATH=.:backend $(PYTHON) scripts/rebase_golden_evidence.py \
		--golden data/evaluation/golden_questions_v1_legacy.json \
		--chunks data/processed/legal_chunks.jsonl \
		--legacy-catalog data/evaluation/legacy_evidence_catalog.json \
		--output data/evaluation/golden_questions_v1_rebased_current_phapdien.json \
		--report data/evaluation/golden_rebase_report.json \
		--strict

golden-audit-rebased:
	PYTHONPATH=.:backend $(PYTHON) scripts/audit_golden_dataset.py \
		--golden data/evaluation/golden_questions_v1_rebased_current_phapdien.json \
		--chunks data/processed/legal_chunks.jsonl \
		--report data/evaluation/golden_v1_rebased_audit.json \
		--strict
