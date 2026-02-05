"""Spring Boot integration endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.recommendation import PlaceRecommendRequest, PlaceRecommendResponse, PlaceRecommendationItem
from app.services.llm import llm_service
from app.services.recommendation import recommend_places

router = APIRouter(tags=["spring-integration"])


@router.post("/recommend-places", response_model=PlaceRecommendResponse)
def recommend_places_for_spring(
    payload: PlaceRecommendRequest,
    db: Session = Depends(get_db),
) -> PlaceRecommendResponse:
    """Spring Boot 호출 형식 엔드포인트."""
    import sys
    
    categories = llm_service.extract_categories_from_query(payload.query)
    location = llm_service.extract_location_from_query(payload.query)
    
    # 디버깅: 추출된 카테고리 출력
    print(f"[DEBUG] 추출된 카테고리: companion={categories.companion}, menu={categories.menu}, mood={categories.mood}, purpose={categories.purpose}", file=sys.stderr)
    
    # 위치 필터링 (우선순위: 요청의 위도/경도 > 쿼리에서 추출한 위치)
    location_filter = None
    if payload.latitude is not None and payload.longitude is not None:
        # 요청에 명시적으로 위도/경도가 있으면 우선 사용
        location_filter = {
            "latitude": payload.latitude,
            "longitude": payload.longitude,
            "radius_km": 10.0,  # 기본 10km 반경
        }
    elif location:
        # 쿼리에서 위치를 추출한 경우 사용
        location_filter = {
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "radius_km": 10.0,  # 기본 10km 반경
        }
    
    items, extracted = recommend_places(db, categories, payload.limit, location_filter)
    
    # Spring Boot 형식으로 변환
    recommendations = []
    for item in items:
        # AI 점수 계산 (간단히 0~100 범위로 정규화)
        # 실제로는 추천 점수를 사용하거나 별도 계산 필요
        ai_score = 85.0  # 임시값, 나중에 실제 점수 계산 로직 추가 가능
        
        recommendations.append(
            PlaceRecommendationItem(
                place_id=str(item.id),
                place_name=item.name,
                category=item.category,
                address=item.origin_address,
                latitude=item.latitude,
                longitude=item.longitude,
                ai_score=ai_score,
                distance_from_midpoint=None,  # Spring에서 계산하도록 None
            )
        )
    
    return PlaceRecommendResponse(
        promise_id=payload.promise_id,
        recommendations=recommendations,
    )
