.PHONY: infra backend runtime-assets runtime-assets-docker test test-ingestion test-corpus vbpl-doctor vbpl-canary vbpl-soak vbpl-fetch vbpl-build-articles vbpl-build-chunks vbpl-audit-chunks vbpl-corpus-pipeline official-docx-release golden-v3-rebase unified-release-validate unified-release

PYTHON ?= .venv/bin/python3
FASTEMBED_CACHE_DIR ?= $(HOME)/.cache/fastembed
VNCORENLP_MODEL_DIR ?= models/vncorenlp
VBPL_TARGET_TOKENS ?= 400
VBPL_MAX_TOKENS ?= 600
VBPL_EXPECTED_CHUNKS = $(shell wc -l < data/processed/vbpl_legal_chunks.jsonl)
VBPL_CANARY_URL := https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-219-2025-nd-cp-quy-dinh-ve-nguoi-lao-dong-nuoc-ngoai-lam-viec-tai-viet-nam--180273
OFFICIAL_DOCX_SOURCE_DIR ?= data/sources/official_docx
DOCX_BUILD_RELEASE_DIR ?= data/releases/labor-law-2026-07-28-candidate

infra:
	docker compose up -d qdrant neo4j

backend:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) -m uvicorn app.main:app --reload

runtime-assets:
	PYTHONPATH=backend:$${PYTHONPATH} $(PYTHON) \
	  -m app.cli.prepare_runtime_assets \
	  --vncorenlp-dir $(VNCORENLP_MODEL_DIR) \
	  --fastembed-cache-dir $(FASTEMBED_CACHE_DIR)

runtime-assets-docker:
	docker compose run --rm runtime-assets

test:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) -m pytest backend/tests

test-ingestion:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) -m pytest -q backend/tests/test_vbpl_ingestion.py

test-corpus:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) -m pytest backend/tests/test_vbpl_article_adapter.py backend/tests/test_vbpl_chunk_pipeline.py backend/tests/test_legal_chunker.py backend/tests/test_build_chunks_cli.py

vbpl-doctor:
	$(PYTHON) scripts/vbpl_portal.py doctor \
	  --portal-url '$(VBPL_CANARY_URL)' \
	  --document-number '219/2025/NĐ-CP'

# Canary proves the text path. It records unresolved official files honestly.
vbpl-canary:
	$(PYTHON) scripts/vbpl_portal.py fetch '219/2025/NĐ-CP' \
	  --portal-url '$(VBPL_CANARY_URL)' \
	  --expected-articles 36 \
	  --output data/raw/vbpl \
	  --attachment-policy best_effort \
	  --resume

# Release gate: ten live reads, zero failures, one stable content hash.
vbpl-soak:
	$(PYTHON) scripts/vbpl_portal.py soak '219/2025/NĐ-CP' \
	  --portal-url '$(VBPL_CANARY_URL)' \
	  --expected-articles 36 \
	  --iterations 10 \
	  --interval-seconds 2 \
	  --max-failure-rate 0 \
	  --report data/raw/vbpl/soak_219.json

vbpl-fetch:
	$(PYTHON) scripts/vbpl_portal.py fetch-config config/vbpl_corpus.json \
	  --output data/raw/vbpl \
	  --resume \
	  --continue-on-error \
	  --retries 4 \
	  --backoff-seconds 1 \
	  --delay-seconds 2 \
	  --run-report data/raw/vbpl/run_manifest.json

vbpl-build-articles:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/build_vbpl_articles.py \
	  --run-manifest data/raw/vbpl/run_manifest.json \
	  --config config/vbpl_corpus.json \
	  --output data/processed/vbpl_articles_raw.json \
	  --report data/quality/vbpl_article_build_report.json \
	  --strict

vbpl-build-chunks:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/build_chunks.py \
	  --input data/processed/vbpl_articles_raw.json \
	  --output data/processed/vbpl_legal_chunks.jsonl \
	  --summary-output data/processed/vbpl_chunking_summary.json \
	  --target-tokens $(VBPL_TARGET_TOKENS) \
	  --max-tokens $(VBPL_MAX_TOKENS) \
	  --strict

vbpl-audit-chunks:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/audit_chunk_quality.py \
	  --corpus data/processed/vbpl_articles_raw.json \
	  --chunks data/processed/vbpl_legal_chunks.jsonl \
	  --output-dir data/processed/vbpl_chunk_quality_audit
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/audit_e5_token_lengths.py \
	  --chunks data/processed/vbpl_legal_chunks.jsonl \
	  --output-dir data/quality/vbpl_e5_token_audit \
	  --cache-dir $(FASTEMBED_CACHE_DIR) \
	  --expected-chunks $(VBPL_EXPECTED_CHUNKS) \
	  --strict
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/audit_vbpl_chunk_quality.py --strict

vbpl-corpus-pipeline:
	$(MAKE) vbpl-build-articles
	$(MAKE) vbpl-build-chunks
	$(MAKE) vbpl-audit-chunks

official-docx-release:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/build_official_docx_release.py \
	  --source-dir $(OFFICIAL_DOCX_SOURCE_DIR) \
	  --base-corpus data/processed/vbpl_articles_raw.json \
	  --raw-root data/raw/official_docx \
	  --release-dir $(DOCX_BUILD_RELEASE_DIR)

golden-v3-rebase:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/rebase_golden_to_release.py \
	  --release-dir $(DOCX_BUILD_RELEASE_DIR)

unified-release-validate:
	PYTHONPATH=.:backend:$${PYTHONPATH} $(PYTHON) scripts/validate_unified_release.py \
	  --release-dir $(DOCX_BUILD_RELEASE_DIR)

unified-release: official-docx-release golden-v3-rebase unified-release-validate

include Makefile.eval.inc
