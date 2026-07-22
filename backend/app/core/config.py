from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Vietnamese Labor Law RAG"

    # CORS
    cors_origins: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173"
    )

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "labor_law"
    dense_embedding_model: str = "intfloat/multilingual-e5-large"
    dense_vector_name: str = "dense"
    dense_vector_size: int = 1024
    sparse_embedding_model: str = "Qdrant/bm25"
    sparse_vector_name: str = "sparse"
    embedding_batch_size: int = 16
    embedding_threads: int = 6
    fastembed_cache_dir: str = ""

    # Retrieval core
    legal_chunks_path: str = "data/processed/legal_chunks.jsonl"
    official_sources_path: str = (
        "data/reference/official_legal_sources.json"
    )
    vncorenlp_model_dir: str = "models/vncorenlp"
    retrieval_expected_chunks: int = 1395
    retrieval_corpus_sha256: str = (
        "27b80463dd6e0f34f767aa6ec1a5b5cd"
        "066b6b7a477c320bb49ef909abcb5e65"
    )
    retrieval_top_k: int = 5
    retrieval_candidate_k: int = 20
    hybrid_rrf_k: int = 60
    hybrid_sparse_weight: float = 1.0
    hybrid_dense_weight: float = 1.0
    bm25_k: float = 1.2
    bm25_b: float = 0.75

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "change_me"

    # LLM generation (OpenRouter is OpenAI-compatible, but is called through
    # httpx so the backend does not need another SDK dependency.)
    llm_provider: str = "openrouter"
    llm_api_key: str = ""  # Backward-compatible fallback only.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openrouter/free"
    openrouter_require_free_model: bool = True
    openrouter_timeout_seconds: float = 90.0
    # Completion budget includes any reasoning tokens used by reasoning models.
    # Keep enough room for a complete, cited Vietnamese answer.
    openrouter_max_tokens: int = 1200
    # A value of 0 disables the explicit reasoning override. For models such as
    # Nemotron 3 Ultra, a small budget prevents hidden reasoning from consuming
    # nearly the entire completion budget.
    openrouter_reasoning_max_tokens: int = 128
    openrouter_exclude_reasoning: bool = True
    openrouter_temperature: float = 0.0
    openrouter_app_url: str = ""
    openrouter_app_title: str = "Vietnamese Labor Law RAG"

    # LangSmith
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "vn-labor-law-rag"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        """Chuyển CORS_ORIGINS từ chuỗi thành danh sách origin."""
        return [
            origin.strip().rstrip("/")
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
