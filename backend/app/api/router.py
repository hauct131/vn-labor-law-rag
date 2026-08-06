from fastapi import APIRouter

from app.api.routes.ask import router as ask_router
from app.api.routes.conversations import router as conversations_router
from app.api.routes.documents import router as documents_router
from app.api.routes.health import router as health_router
from app.api.routes.sources import router as sources_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(ask_router)
api_router.include_router(sources_router)
api_router.include_router(documents_router)
api_router.include_router(conversations_router)
