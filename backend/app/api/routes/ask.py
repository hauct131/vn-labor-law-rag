"""Public question-answering endpoint."""

from fastapi import APIRouter, Depends, HTTPException

from ...chains.generation_chain import (
    GenerationConfigurationError,
    GenerationProviderError,
)
from ...retrieval.models import (
    RetrievalBackendError,
    RetrievalConfigurationError,
)
from ...schemas.ask import AskRequest, AskResponse
from ...services.rag_service import RAGService, get_rag_service


router = APIRouter(tags=["Question answering"])


@router.post("/ask", response_model=AskResponse)
async def ask_question(
    payload: AskRequest,
    service: RAGService = Depends(get_rag_service),
) -> AskResponse:
    try:
        return await service.ask(payload.question, payload.method)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except GenerationConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except GenerationProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except RetrievalConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RetrievalBackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Không thể khởi tạo bộ truy hồi: {exc}",
        ) from exc
