"""Spring Boot integration endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.place_summary_embedding import PlaceSummaryEmbedding
from app.schemas.recommendation import PlaceRecommendRequest, PlaceRecommendResponse, PlaceRecommendationItem
from app.services.llm import llm_service
from app.services.recommendation import CATEGORY_WEIGHTS, recommend_places, MAX_RAW_SCORE, build_profile_vectors, recommend_places_by_profile
from app.core.config import settings

router = APIRouter(tags=["spring-integration"])


@router.post("/recommend-places", response_model=PlaceRecommendResponse)
def recommend_places_for_spring(
    payload: PlaceRecommendRequest,
    db: Session = Depends(get_db),
) -> PlaceRecommendResponse:
    """Spring Boot 호출 형식. 요청에서 추출된 카테고리만 유사도 검색하며, 없으면 해당 카테고리는 스킵. PK+ai_score만 반환."""
    import logging
    logger = logging.getLogger(__name__)
    
    # 위치 필터링 (우선순위: 요청의 위도/경도 > 쿼리에서 추출한 위치)
    location_filter = None
    if payload.latitude is not None and payload.longitude is not None:
        location_filter = {
            "latitude": payload.latitude,
            "longitude": payload.longitude,
            "radius_km": settings.recommendation_default_radius_km,
        }

    query_is_empty = not (payload.query or "").strip()
    has_history = bool(payload.past_place_ids)

    if query_is_empty and has_history:
        # 쿼리 없음 + 히스토리 있음 → 프로필 벡터 기반 개인화 추천
        logger.info("프로필 기반 추천 모드: user_id=%s, past_place_ids=%s", payload.user_id, payload.past_place_ids)

        profile_vectors = build_profile_vectors(db, payload.past_place_ids)

        # 위치 필터로 후보 장소 ID 추출
        candidate_place_ids = None
        if location_filter:
            from math import radians, cos, sin, asin, sqrt
            from sqlalchemy import select as sa_select
            from app.models.place import Place
            R = 6371.0
            lat1 = radians(location_filter["latitude"])
            lon1 = radians(location_filter["longitude"])
            radius_km = location_filter.get("radius_km") or settings.recommendation_default_radius_km
            all_places = db.execute(sa_select(Place)).scalars().all()
            candidate_place_ids = []
            for place in all_places:
                if place.latitude is None or place.longitude is None:
                    continue
                lat2, lon2 = radians(place.latitude), radians(place.longitude)
                dlat, dlon = lat2 - lat1, lon2 - lon1
                a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
                if R * 2 * asin(sqrt(a)) <= radius_km:
                    candidate_place_ids.append(place.id)

        items, place_scores, place_scores_by_category = recommend_places_by_profile(
            db, profile_vectors, payload.limit or 10, candidate_place_ids
        )
        from app.schemas.review import CategoryInfo
        extracted = CategoryInfo()

    else:
        # 쿼리 있음 → 기존 방식
        location = llm_service.extract_location_from_query(payload.query) if not query_is_empty else None
        categories = llm_service.extract_categories_from_query(payload.query) if not query_is_empty else __import__('app.schemas.review', fromlist=['CategoryInfo']).CategoryInfo()

        logger.info(
            "쿼리 기반 추천 모드: companion=%s, menu=%s, mood=%s, purpose=%s, place_type=%s",
            categories.companion, categories.menu, categories.mood, categories.purpose, getattr(categories, "place_type", None),
        )

        if not location_filter and location:
            location_filter = {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "radius_km": settings.recommendation_default_radius_km,
            }

        items, extracted, place_scores, place_scores_by_category = recommend_places(
            db, categories, payload.limit, location_filter, payload.tab
        )

    # 절대점수: 만점 100점, raw를 MAX_RAW_SCORE 기준으로 환산 후 소수 둘째자리
    def to_absolute_score(raw: float) -> float:
        if MAX_RAW_SCORE <= 0:
            return 0.0
        return min(100.0, round((raw / MAX_RAW_SCORE) * 100.0, 2))

    # 중간지점이 있으면 장소까지 거리(km) 계산
    def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        import math
        R = 6371.0
        lat1, lon1 = math.radians(lat1), math.radians(lon1)
        lat2, lon2 = math.radians(lat2), math.radians(lon2)
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.asin(math.sqrt(a))
        return round(R * c, 2)

    center_lat = location_filter["latitude"] if location_filter else None
    center_lon = location_filter["longitude"] if location_filter else None

    place_ids = [item.id for item in items]
    place_summary_map: dict[int, str] = {}
    if place_ids:
        rows = db.execute(
            select(PlaceSummaryEmbedding.place_id, PlaceSummaryEmbedding.summary_text)
            .where(PlaceSummaryEmbedding.place_id.in_(place_ids))
            .distinct(PlaceSummaryEmbedding.place_id)
        ).fetchall()
        place_summary_map = {place_id: summary for place_id, summary in rows if summary}

    recommendations = []
    for item in items:
        raw = place_scores.get(item.id, 0.0)
        by_cat = place_scores_by_category.get(item.id, {})
        summary_text = place_summary_map.get(item.id)
        logger.info(
            "[추천 점수] place_id=%s name=%s category=%s total=%.4f | by_category=%s | summary_exists=%s",
            item.id, item.name, item.category, raw, by_cat, bool(summary_text),
        )
        dist = None
        if center_lat is not None and center_lon is not None and item.latitude is not None and item.longitude is not None:
            dist = distance_km(center_lat, center_lon, item.latitude, item.longitude)
        recommendations.append(
            PlaceRecommendationItem(
                place_id=str(item.id),
                ai_score=to_absolute_score(raw),
                similarity_score=round(raw, 4),
                distance_from_midpoint=dist,
                place_name=item.name,
                category=item.category,
                address=item.road_address,
                image_url=getattr(item, "image_url", None),
                ai_summary=getattr(item, "ai_summary", None),
                latitude=item.latitude,
                longitude=item.longitude,
                summary_text=summary_text,
            )
        )
    
    return PlaceRecommendResponse(
        promise_id=payload.promise_id,
        recommendations=recommendations,
    )
