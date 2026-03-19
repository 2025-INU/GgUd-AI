"""Recommendation endpoints."""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.recommendation import (
    RecommendationRequest,
    RecommendationResponse,
    RecommendationDebug,
    RecommendationItem,
)
from app.services.llm import llm_service
from app.services.recommendation import recommend_places, MAX_RAW_SCORE

router = APIRouter(prefix="/recommendations", tags=["recommendations"])
logger = logging.getLogger(__name__)


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
    
    items, extracted, place_scores, place_scores_by_category = recommend_places(
        db, categories, payload.limit, location_filter
    )

    def to_absolute_score(raw: float) -> float:
        if MAX_RAW_SCORE <= 0:
            return 0.0
        return min(100.0, round((raw / MAX_RAW_SCORE) * 100.0, 2))

    result_items = []
    for item in items:
        raw = place_scores.get(item.id, 0.0)
        by_cat = place_scores_by_category.get(item.id, {})
        logger.info(
            "[추천 점수] place_id=%s name=%s category=%s total=%.4f | by_category=%s",
            item.id, item.name, item.category, raw, by_cat,
        )
        result_items.append(
            RecommendationItem(
                **item.model_dump(),
                ai_score=to_absolute_score(raw),
                similarity_score=round(raw, 4),
            )
        )

    return RecommendationResponse(
        items=result_items,
        meta=RecommendationDebug(extracted_categories=extracted),
    )




