from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Vietnamese Labor Law RAG"

    # CORS
    cors_origins: str = (
        "http://localhost:5173,"
        "http://127.0.0.1:5173"
    )

    # Relational application data. Docker Compose overrides this with
    # PostgreSQL; SQLite keeps direct local runs and tests self-contained.
    database_url: str = "sqlite+pysqlite:///./application.db"
    database_echo: bool = False
    database_auto_create: bool = True

    # Account and server-side session authentication
    session_cookie_name: str = "legal_rag_session"
    csrf_cookie_name: str = "legal_rag_csrf"
    session_cookie_secure: bool = False
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_ttl_seconds: int = Field(default=604800, ge=300, le=31536000)
    session_touch_interval_seconds: int = Field(default=300, ge=0, le=86400)
    password_pbkdf2_iterations: int = Field(
        default=600000,
        ge=100000,
        le=5000000,
    )

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    # Retrieval always uses an alias. Physical collections are versioned so a
    # new corpus can be indexed and verified without mutating the old index.
    qdrant_collection: str = "labor_law_active"
    qdrant_expected_collection: str = "labor_law_canonical_word_20260804_fdbec539"
    qdrant_api_key: str = ""
    qdrant_readiness_timeout_seconds: float = 3.0
    runtime_readiness_ttl_seconds: float = 30.0
    runtime_readiness_failure_ttl_seconds: float = 5.0
    runtime_java_timeout_seconds: float = 3.0
    runtime_smoke_query: str = "quyền của người lao động"
    dense_embedding_model: str = "intfloat/multilingual-e5-large"
    dense_vector_name: str = "dense"
    dense_vector_size: int = 1024
    sparse_embedding_model: str = "Qdrant/bm25"
    sparse_vector_name: str = "sparse"
    embedding_batch_size: int = 16
    embedding_threads: int = 6
    fastembed_cache_dir: str = ""

    # Retrieval core
    legal_chunks_path: str = (
        "data/releases/labor-law-canonical-word-20260804-164432-candidate/canonical_chunks.jsonl"
    )
    corpus_release_manifest_path: str = (
        "data/releases/labor-law-canonical-word-20260804-164432-candidate/manifest.json"
    )
    e5_audit_summary_path: str = (
        "data/releases/labor-law-canonical-word-20260804-164432-candidate/"
        "e5_audit/summary.json"
    )
    corpus_release_id: str = "labor-law-canonical-word-20260804-164432-candidate"
    corpus_require_authority_approval: bool = True
    official_sources_path: str = (
        "data/reference/official_legal_sources.json"
    )
    canonical_articles_path: str = (
        "data/releases/labor-law-canonical-word-20260804-164432-candidate/canonical_articles.json"
    )
    vncorenlp_model_dir: str = "models/vncorenlp"
    retrieval_expected_chunks: int = 804
    retrieval_corpus_sha256: str = (
        "fdbec539efbfb3f4aa3cb3962046321e3"
        "a402150d93516ef4256934972c70307"
    )
    retrieval_top_k: int = 5
    retrieval_candidate_k: int = Field(default=50, ge=1, le=200)
    generation_context_k: int = Field(default=10, ge=1, le=50)
    hybrid_rrf_k: int = 60
    hybrid_sparse_weight: float = 0.1
    hybrid_dense_weight: float = 0.9
    bm25_k: float = 1.2
    bm25_b: float = 0.75

    @model_validator(mode="after")
    def validate_retrieval_and_llm_settings(self) -> "Settings":
        if self.retrieval_candidate_k < self.generation_context_k:
            raise ValueError(
                "retrieval_candidate_k must be greater than or equal to generation_context_k"
            )
        if self.openrouter_retry_max_tokens < self.openrouter_max_tokens:
            raise ValueError(
                "openrouter_retry_max_tokens must be greater than or equal to openrouter_max_tokens"
            )
        return self

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
    openrouter_retry_max_tokens: int = Field(default=2000, ge=1200, le=4096)
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
