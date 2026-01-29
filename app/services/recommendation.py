"""Core recommendation logic."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.place import Place
from app.models.review_embedding import PlaceEmbedding
from app.schemas.review import CategoryInfo
from app.schemas.place import PlaceOut
from app.services.llm import llm_service


CATEGORY_KEYS = ("companion", "menu", "mood", "purpose")

# 카테고리별 가중치 (고정값)
CATEGORY_WEIGHTS = {
    "companion": 1.0,  # 동행자 (가장 중요)
    "menu": 0.8,       # 메뉴
    "mood": 0.6,       # 분위기
    "purpose": 0.4,    # 목적
}


def upsert_place(db: Session, data: dict) -> Place:
    """Insert or update place metadata."""
    from datetime import datetime
    
    try:
        place = db.get(Place, data["id"])
        if place:
            for field in ("name", "category", "origin_address", "latitude", "longitude"):
                setattr(place, field, data[field])
            place.updated_at = datetime.now()
        else:
            # 새로 생성할 때 crawled_at 설정
            if "crawled_at" not in data:
                data["crawled_at"] = datetime.now()
            place = Place(**data)
            db.add(place)
        db.commit()
        db.refresh(place)
        return place
    except Exception:
        db.rollback()
        raise


def refresh_embeddings(db: Session, place_id: int, content: str) -> tuple[CategoryInfo, int]:
    """Extract categories from review text and store embeddings."""
    import sys
    
    def split_values(value: str | None) -> list[str]:
        """쉼표로 구분된 값을 분리하여 리스트로 반환"""
        if not value:
            return []
        # 쉼표로 분리하고 각 값을 trim
        return [v.strip() for v in str(value).split(",") if v.strip()]
    
    categories = llm_service.extract_categories(content)
    inserted = 0
    skipped = 0
    
    for key in CATEGORY_KEYS:
        value = getattr(categories, key)
        if not value:
            continue
        
        # 쉼표로 구분된 값들을 분리
        values = split_values(value)
        
        # 각 값을 개별적으로 임베딩 생성 및 저장
        for single_value in values:
            exists = (
                db.query(PlaceEmbedding)
                .filter(
                    PlaceEmbedding.place_id == place_id,
                    PlaceEmbedding.category == key,
                    PlaceEmbedding.value_text == single_value,
                )
                .first()
            )
            if exists:
                skipped += 1
                print(f"[DEBUG] place_id={place_id}, {key}=\"{single_value}\" → 중복 스킵", file=sys.stderr)
                continue
            
            embedding = llm_service.embed_text(single_value)
            embedding_row = PlaceEmbedding(
                place_id=place_id,
                category=key,
                value_text=single_value,
                embedding=embedding,
            )
            db.add(embedding_row)
            inserted += 1
            print(f"[DEBUG] place_id={place_id}, {key}=\"{single_value}\" → 임베딩 생성", file=sys.stderr)
    
    db.commit()
    return categories, inserted


def _similar_places_stmt(category: str, query_vector: list[float], limit: int) -> Select:
    """Return a SQLAlchemy statement that finds similar places."""
    distance = PlaceEmbedding.embedding.cosine_distance(query_vector)
    return (
        select(PlaceEmbedding.place_id, func.min(distance).label("score"))
        .where(PlaceEmbedding.category == category)
        .group_by(PlaceEmbedding.place_id)
        .order_by("score")
        .limit(limit)
    )


def recommend_places(
    db: Session,
    categories: CategoryInfo,
    limit: int | None = None,
    location_filter: dict[str, float] | None = None,  # {"latitude": float, "longitude": float, "radius_km": float}
) -> tuple[list[PlaceOut], CategoryInfo]:
    """Return recommended places based on category embeddings with weighted scoring."""
    limit = limit or settings.recommendation_top_k
    
    # 각 카테고리별로 유사 장소 찾기 (가중치 적용)
    place_scores: dict[int, float] = {}  # place_id -> weighted_score
    
    for key in CATEGORY_KEYS:
        value = getattr(categories, key)
        if not value:
            continue
        
        weight = CATEGORY_WEIGHTS.get(key, 1.0)
        vector = llm_service.embed_text(value)
        
        # 각 카테고리별로 더 많은 후보를 찾아서 가중치 적용
        stmt = _similar_places_stmt(key, vector, limit * 3)  # 더 넓게 후보 수집
        rows = db.execute(stmt).fetchall()
        
        for place_id, distance in rows:
            # cosine_distance는 0에 가까울수록 유사함 (0~2 범위)
            # 가중치 적용: 거리가 작을수록 높은 점수
            similarity_score = 1.0 - (distance / 2.0)  # 0~1 범위로 정규화
            weighted_score = similarity_score * weight
            
            if place_id not in place_scores:
                place_scores[place_id] = 0.0
            place_scores[place_id] += weighted_score
    
    if not place_scores:
        return [], categories
    
    # 지역 필터링 적용 (있는 경우)
    if location_filter:
        places_query = select(Place).where(Place.id.in_(list(place_scores.keys())))
        all_candidates = db.execute(places_query).scalars().all()
        
        filtered_scores = {}
        lat = location_filter["latitude"]
        lon = location_filter["longitude"]
        radius_km = location_filter.get("radius_km", 10.0)  # 기본 10km
        
        for place in all_candidates:
            # 하버사인 공식으로 거리 계산
            from math import radians, cos, sin, asin, sqrt
            R = 6371.0  # 지구 반지름 (km)
            
            lat1, lon1 = radians(lat), radians(lon)
            lat2, lon2 = radians(place.latitude), radians(place.longitude)
            
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            
            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
            c = 2 * asin(sqrt(a))
            distance_km = R * c
            
            if distance_km <= radius_km:
                filtered_scores[place.id] = place_scores[place.id]
        
        place_scores = filtered_scores
    
    # 점수 순으로 정렬하여 상위 limit개 선택
    sorted_place_ids = sorted(place_scores.items(), key=lambda x: x[1], reverse=True)[:limit]
    
    if not sorted_place_ids:
        return [], categories
    
    top_place_ids = [place_id for place_id, _score in sorted_place_ids]
    
    places: Iterable[Place] = (
        db.execute(select(Place).where(Place.id.in_(top_place_ids))).scalars().all()
    )
    ordered_places = sorted(places, key=lambda p: top_place_ids.index(p.id))
    return [PlaceOut.model_validate(p) for p in ordered_places], categories


