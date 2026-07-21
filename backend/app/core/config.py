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

    # LLM
    llm_provider: str = "not_configured"
    llm_api_key: str = ""

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
