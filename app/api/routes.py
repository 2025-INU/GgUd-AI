"""Root API router."""

from fastapi import APIRouter

from app.api.endpoints import places, recommendations

router = APIRouter()


@router.get("/health", tags=["health"])
async def health_check() -> dict[str, str]:
    """Simple health check endpoint."""
    return {"status": "ok"}


router.include_router(places.router)
router.include_router(recommendations.router)

# spring_integration 은 main.py 에서 루트 경로로 직접 등록됨
