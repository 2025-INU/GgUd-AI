"""FastAPI application entry point."""

import logging

from dotenv import load_dotenv

from fastapi import FastAPI

# Load environment variables from .env file
load_dotenv()

# uvicorn 터미널에서 앱 로그(INFO)가 보이도록 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(name)s: %(message)s",
)

# init_db()가 create_all()을 호출하므로, 여기서는 테이블 생성에 필요한 모델만 import해서 등록한다.
from app.models.place import Place  # noqa: F401
from app.models.place_summary_embedding import PlaceSummaryEmbedding  # noqa: F401
from app.api.routes import router
from app.api.endpoints import spring_integration
from app.core.config import settings
from app.db.init_db import init_db

app = FastAPI(title=settings.project_name)
app.include_router(router, prefix=settings.api_v1_prefix)

# Spring Boot 호출을 위한 루트 경로 엔드포인트 (prefix 없이)
app.include_router(spring_integration.router)


@app.on_event("startup")
def on_startup() -> None:
    """Initialize database artifacts."""
    init_db()


@app.get("/", tags=["root"])
async def root() -> dict[str, str]:
    """Basic sanity endpoint."""
    return {"message": "Meetup Recommender API is running"}


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Health check endpoint for Docker."""
    return {"status": "healthy"}


