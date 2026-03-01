"""Recommendation endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.recommendation import (
    RecommendationRequest,
    RecommendationResponse,
    RecommendationDebug,
)
from app.services.llm import llm_service
from app.services.recommendation import recommend_places

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post("", response_model=RecommendationResponse)
def recommend(payload: RecommendationRequest, db: Session = Depends(get_db)) -> RecommendationResponse:
    """Recommend places based on a natural language query."""
    categories = llm_service.extract_categories_from_query(payload.query)
    location = llm_service.extract_location_from_query(payload.query)
    
    # 위치 필터링 (있는 경우만)
    location_filter = None
    if location:
        location_filter = {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "radius_km": 10.0,  # 기본 10km 반경
        }
    
    items, extracted, _ = recommend_places(db, categories, payload.limit, location_filter)
    return RecommendationResponse(
        items=items,
        meta=RecommendationDebug(extracted_categories=extracted),
    )




