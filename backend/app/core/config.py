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