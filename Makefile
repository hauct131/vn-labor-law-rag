.PHONY: infra backend test test-ingestion vbpl-doctor vbpl-canary vbpl-soak vbpl-fetch

VBPL_CANARY_URL := https://vbpl.vn/van-ban/chi-tiet/nghi-dinh-so-219-2025-nd-cp-quy-dinh-ve-nguoi-lao-dong-nuoc-ngoai-lam-viec-tai-viet-nam--180273

infra:
	docker compose up -d qdrant neo4j

backend:
	cd backend && uvicorn app.main:app --reload

test:
	PYTHONPATH=.:backend:$${PYTHONPATH} python -m pytest backend/tests

test-ingestion:
	PYTHONPATH=.:backend:$${PYTHONPATH} python -m pytest -q backend/tests/test_vbpl_ingestion.py

vbpl-doctor:
	python scripts/vbpl_portal.py doctor \
	  --portal-url '$(VBPL_CANARY_URL)' \
	  --document-number '219/2025/NĐ-CP'

# Canary proves the text path. It records unresolved official files honestly.
vbpl-canary:
	python scripts/vbpl_portal.py fetch '219/2025/NĐ-CP' \
	  --portal-url '$(VBPL_CANARY_URL)' \
	  --expected-articles 36 \
	  --output data/raw/vbpl \
	  --attachment-policy best_effort \
	  --resume

# Release gate: ten live reads, zero failures, one stable content hash.
vbpl-soak:
	python scripts/vbpl_portal.py soak '219/2025/NĐ-CP' \
	  --portal-url '$(VBPL_CANARY_URL)' \
	  --expected-articles 36 \
	  --iterations 10 \
	  --interval-seconds 2 \
	  --max-failure-rate 0 \
	  --report data/raw/vbpl/soak_219.json

vbpl-fetch:
	python scripts/vbpl_portal.py fetch-config config/vbpl_corpus.json \
	  --output data/raw/vbpl \
	  --resume \
	  --continue-on-error \
	  --retries 4 \
	  --backoff-seconds 1 \
	  --delay-seconds 2 \
	  --run-report data/raw/vbpl/run_manifest.json
