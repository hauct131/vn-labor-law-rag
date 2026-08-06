from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.db.database import initialize_database


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.database_auto_create:
        initialize_database()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="Vietnamese Labor Law RAG API",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Client-Id"],
)

app.include_router(api_router, prefix="/api")


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "message": settings.app_name,
        "docs": "/docs",
        "live": "/api/live",
        "ready": "/api/ready",
    }
