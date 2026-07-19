.PHONY: infra backend test

infra:
	docker compose up -d qdrant neo4j

backend:
	cd backend && uvicorn app.main:app --reload

test:
	PYTHONPATH=.:backend:$${PYTHONPATH} python -m pytest backend/tests
