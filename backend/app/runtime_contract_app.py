"""Minimal runtime app used by the contract-review E2E without optional RAG imports."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.auth import router as auth_router
from app.api.routes.contract_reviews import router as contract_review_router
from app.core.config import settings
from app.db.database import initialize_database


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="Contract Review Runtime E2E", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Client-Id", "X-CSRF-Token"],
)
app.include_router(auth_router, prefix="/api")
app.include_router(contract_review_router, prefix="/api")


@app.get("/api/live")
def live() -> dict[str, str]:
    return {"status": "live"}
