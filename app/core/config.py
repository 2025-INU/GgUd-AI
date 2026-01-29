"""Application configuration."""

from functools import lru_cache
import os

from pydantic import BaseModel, AnyUrl


class Settings(BaseModel):
    """Application settings (env 값이 있으면 사용, 없으면 기본값)."""

    project_name: str = os.getenv("PROJECT_NAME", "Meetup Recommender API")
    api_v1_prefix: str = os.getenv("API_V1_PREFIX", "/api/v1")

    database_url: AnyUrl | str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres:postgres@localhost:5432/meetup",
    )
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_embedding_model: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    openai_response_model: str = os.getenv("OPENAI_RESPONSE_MODEL", "gpt-4o-mini")
    recommendation_top_k: int = int(os.getenv("RECOMMENDATION_TOP_K", "5"))


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()


settings = get_settings()


