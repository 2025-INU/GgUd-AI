"""Core recommendation logic."""

from __future__ import annotations

from typing import Iterable

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.place import Place
from app.models.review import Review
from app.models.review_embedding import PlaceEmbedding
from app.models.place_summary_embedding import PlaceSummaryEmbedding
from app.schemas.review import CategoryInfo
from app.schemas.place import PlaceOut
from app.services.llm import llm_service


CATEGORY_KEYS = ("companion", "menu", "mood", "purpose")

# 카테고리별 가중치 (고정값)
CATEGORY_WEIGHTS = {
    "menu": 0.4,       # 메뉴
    "companion": 0.2,  # 동행자
    "mood": 0.2,       # 분위기
    "purpose": 0.2,    # 목적
}

# ai_score 0~100 환산용 (가중치 합 = 1.0이면 만점 1.0)
MAX_RAW_SCORE = sum(CATEGORY_WEIGHTS.values())

# 메뉴가 구체적으로 지정됐을 때, 이 거리(코사인 거리) 이내인 리뷰 메뉴 임베딩이 있는 장소만 후보로 둠.
# (거리 0 = 동일, 2 = 반대. 0.45 이하면 유사도 약 0.775 이상으로 실제 그 메뉴를 다루는 장소로 간주)
MENU_MATCH_DISTANCE_THRESHOLD = 0.45
# 메뉴 지정 시, 이 가중 점수(menu 기여분) 미만인 장소는 최종 추천에서 제외
MENU_MIN_WEIGHTED_SCORE = 0.28


def _similar_places_stmt(
    category: str,
    query_vector: list[float],
    limit: int,
    candidate_place_ids: list[int] | None = None,
) -> Select:
    """장소 요약 임베딩 단위로 쿼리와 코사인 거리 계산 후 장소별 랭킹."""
    distance = PlaceSummaryEmbedding.embedding.cosine_distance(query_vector)
    stmt = (
        select(
            PlaceSummaryEmbedding.place_id,
            func.avg(distance).label("avg_distance"),
        )
        .where(PlaceSummaryEmbedding.category == category)
    )
    if candidate_place_ids:
        stmt = stmt.where(PlaceSummaryEmbedding.place_id.in_(candidate_place_ids))
    return (
        stmt.group_by(PlaceSummaryEmbedding.place_id)
        .order_by("avg_distance")
        .limit(limit * 5)
    )


def _split_values(value: str | None) -> list[str]:
    if not value:
        return []
    values = [v.strip() for v in str(value).split(",") if v.strip()]
    return list(dict.fromkeys(values))


def upsert_place(db: Session, data: dict) -> Place:
    """Insert or update place metadata."""
    from datetime import datetime

    allowed = (
        "name",
        "category",
        "road_address",
        "image_url",
        "latitude",
        "longitude",
        "review_count",
        "crawled_at",
        "updated_at",
    )
    now = datetime.now()
    try:
        place = db.get(Place, data["id"])
        if place:
            for field in allowed:
                if field in data:
                    setattr(place, field, data[field])
            place.updated_at = data.get("updated_at") or now
        else:
            payload = {"id": data["id"]}
            for field in allowed:
                if field in data:
                    payload[field] = data[field]
            if "crawled_at" not in payload:
                payload["crawled_at"] = now
            if "updated_at" not in payload:
                payload["updated_at"] = now
            if "review_count" not in payload:
                payload["review_count"] = 0
            place = Place(**payload)
            db.add(place)
        db.commit()
        db.refresh(place)
        return place
    except Exception:
        db.rollback()
        raise


def refresh_embeddings(db: Session, place_id: int, review_id: int, content: str) -> tuple[CategoryInfo, int]:
    """Extract categories from review text and store embeddings (리뷰별 각각 저장)."""
    import sys
    
    def split_values(value: str | None) -> list[str]:
        if not value:
            return []
        # 동일 리뷰 내 중복 토큰(예: "친구, 친구, 친구")은 한 번만 처리
        values = [v.strip() for v in str(value).split(",") if v.strip()]
        return list(dict.fromkeys(values))
    
    categories = llm_service.extract_categories(content)
    inserted = 0
    
    for key in CATEGORY_KEYS:
        value = getattr(categories, key)
        if not value:
            continue
        values = split_values(value)
        for single_value in values:
            exists = (
                db.query(PlaceEmbedding)
                .filter(
                    PlaceEmbedding.place_id == place_id,
                    PlaceEmbedding.review_id == review_id,
                    PlaceEmbedding.category == key,
                    PlaceEmbedding.value_text == single_value,
                )
                .first()
            )
            if exists:
                continue
            embedding = llm_service.embed_text(single_value)
            embedding_row = PlaceEmbedding(
                place_id=place_id,
                review_id=review_id,
                category=key,
                value_text=single_value,
                embedding=embedding,
            )
            db.add(embedding_row)
            inserted += 1
            print(f"[DEBUG] place_id={place_id}, review_id={review_id}, {key}=\"{single_value}\" → 임베딩 생성", file=sys.stderr)
    db.commit()
    return categories, inserted


def refresh_place_summary_embeddings(db: Session, place_id: int) -> tuple[str, CategoryInfo, int]:
    """장소의 전체 리뷰를 1개 요약문으로 만들고 카테고리 임베딩을 재생성."""
    place = db.get(Place, place_id)
    if not place:
        return "", CategoryInfo(), 0

    try:
        reviews = (
            db.query(Review.content)
            .filter(Review.place_id == place_id)
            .all()
        )
    except Exception:
        # 신규 파이프라인에서 reviews 테이블이 없을 수 있음.
        return "", CategoryInfo(), 0
    review_texts = [r[0] for r in reviews if r and r[0] and r[0].strip()]
    if not review_texts:
        return "", CategoryInfo(), 0

    return refresh_place_summary_embeddings_from_review_texts(
        db=db,
        place_id=place_id,
        review_texts=review_texts,
        place_name=place.name,
    )


def refresh_place_summary_embeddings_from_review_texts(
    db: Session,
    place_id: int,
    review_texts: list[str],
    place_name: str | None = None,
) -> tuple[str, CategoryInfo, int]:
    """리뷰 텍스트 리스트를 요약해 장소 요약 임베딩을 재생성."""
    cleaned = [text.strip() for text in review_texts if text and text.strip()]
    if not cleaned:
        return "", CategoryInfo(), 0

    summary_text = llm_service.summarize_reviews(cleaned, place_name)
    if not summary_text:
        return "", CategoryInfo(), 0

    categories = llm_service.extract_categories(summary_text)
    db.query(PlaceSummaryEmbedding).filter(PlaceSummaryEmbedding.place_id == place_id).delete()

    inserted = 0
    for key in CATEGORY_KEYS:
        values = _split_values(getattr(categories, key))
        for single_value in values:
            embedding = llm_service.embed_text(single_value)
            db.add(
                PlaceSummaryEmbedding(
                    place_id=place_id,
                    category=key,
                    value_text=single_value,
                    summary_text=summary_text,
                    embedding=embedding,
                )
            )
            inserted += 1

    db.commit()
    return summary_text, categories, inserted


def recommend_places(
    db: Session,
    categories: CategoryInfo,
    limit: int | None = None,
    location_filter: dict[str, float] | None = None,
    tab: str | None = None,
) -> tuple[list[PlaceOut], CategoryInfo, dict[int, float], dict[int, dict[str, float]]]:
    """위치 필터가 있으면 먼저 위도/경도 반경으로 후보를 줄이고, 그 안에서만 카테고리별 벡터 검색.
    장소 요약 카테고리 임베딩에 대해 유사도 검색 후, 가중합으로 랭킹.
    반환: (places, extracted_categories, place_scores, place_scores_by_category)."""
    limit = limit or settings.recommendation_top_k
    
    candidate_place_ids: list[int] | None = None
    if location_filter:
        from math import radians, cos, sin, asin, sqrt
        R = 6371.0
        lat = location_filter["latitude"]
        lon = location_filter["longitude"]
        radius_km = location_filter.get("radius_km") or settings.recommendation_default_radius_km
        all_places = db.execute(select(Place)).scalars().all()
        candidate_place_ids = []
        lat1, lon1 = radians(lat), radians(lon)
        for place in all_places:
            if place.latitude is None or place.longitude is None:
                continue
            lat2, lon2 = radians(place.latitude), radians(place.longitude)
            dlat = lat2 - lat1
            dlon = lon2 - lon1
            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
            c = 2 * asin(sqrt(a))
            if R * c <= radius_km:
                candidate_place_ids.append(place.id)
        if not candidate_place_ids:
            return [], categories, {}, {}

    # 탭 필터: places.category(DB 원본 카테고리) 기준으로 후보를 제한
    normalized_tab = (tab or "ALL").upper()
    if normalized_tab in {"CAFE", "BAR", "RESTAURANT"}:
        stmt = select(Place.id)
        if candidate_place_ids is not None:
            stmt = stmt.where(Place.id.in_(candidate_place_ids))

        if normalized_tab == "CAFE":
            stmt = stmt.where(
                func.lower(Place.category).contains("카페")
                | func.lower(Place.category).contains("coffee")
                | func.lower(Place.category).contains("커피")
                | func.lower(Place.category).contains("디저트")
                | func.lower(Place.category).contains("베이커리")
            )
        elif normalized_tab == "BAR":
            stmt = stmt.where(
                func.lower(Place.category).contains("술")
                | func.lower(Place.category).contains("주점")
                | func.lower(Place.category).contains("bar")
                | func.lower(Place.category).contains("pub")
                | func.lower(Place.category).contains("이자카야")
                | func.lower(Place.category).contains("포차")
                | func.lower(Place.category).contains("와인")
            )
        else:  # RESTAURANT
            cafe_or_bar = (
                func.lower(Place.category).contains("카페")
                | func.lower(Place.category).contains("coffee")
                | func.lower(Place.category).contains("커피")
                | func.lower(Place.category).contains("디저트")
                | func.lower(Place.category).contains("베이커리")
                | func.lower(Place.category).contains("술")
                | func.lower(Place.category).contains("주점")
                | func.lower(Place.category).contains("bar")
                | func.lower(Place.category).contains("pub")
                | func.lower(Place.category).contains("이자카야")
                | func.lower(Place.category).contains("포차")
                | func.lower(Place.category).contains("와인")
            )
            stmt = stmt.where(~cafe_or_bar)

        tab_candidate_ids = [row[0] for row in db.execute(stmt).fetchall()]
        if not tab_candidate_ids:
            return [], categories, {}, {}
        candidate_place_ids = tab_candidate_ids

    # LLM이 추출한 업종(place_type)이 있으면 해당 업종 장소만 후보로 제한
    if getattr(categories, "place_type", None):
        place_type = (categories.place_type or "").strip()
        if place_type:
            stmt_place_type = select(Place.id).where(Place.category.ilike(f"%{place_type}%"))
            if candidate_place_ids is not None:
                stmt_place_type = stmt_place_type.where(Place.id.in_(candidate_place_ids))
            candidate_place_ids = [row[0] for row in db.execute(stmt_place_type).fetchall()]
            if not candidate_place_ids:
                return [], categories, {}, {}

    # 메뉴가 구체적으로 지정됐을 때: 해당 메뉴와 유사한 요약 메뉴 임베딩이 있는 장소만 후보로 제한
    if categories.menu and (categories.menu or "").strip():
        menu_vector = llm_service.embed_text((categories.menu or "").strip())
        dist_expr = PlaceSummaryEmbedding.embedding.cosine_distance(menu_vector)
        stmt_menu = (
            select(PlaceSummaryEmbedding.place_id)
            .where(PlaceSummaryEmbedding.category == "menu")
            .where(dist_expr <= MENU_MATCH_DISTANCE_THRESHOLD)
            .distinct()
        )
        if candidate_place_ids is not None:
            stmt_menu = stmt_menu.where(PlaceSummaryEmbedding.place_id.in_(candidate_place_ids))
        menu_qualified_ids = [row[0] for row in db.execute(stmt_menu).fetchall()]
        if menu_qualified_ids:
            candidate_place_ids = menu_qualified_ids

    menu_specified = bool(categories.menu and (categories.menu or "").strip())
    place_scores: dict[int, float] = {}
    place_scores_by_category: dict[int, dict[str, float]] = {}
    for key in CATEGORY_KEYS:
        value = getattr(categories, key)
        if not value:
            continue
        weight = CATEGORY_WEIGHTS.get(key, 1.0)
        vector = llm_service.embed_text(value)
        stmt = _similar_places_stmt(key, vector, limit * 5, candidate_place_ids)
        rows = db.execute(stmt).fetchall()
        for place_id, avg_distance in rows:
            similarity_score = 1.0 - (avg_distance / 2.0)
            weighted_score = similarity_score * weight
            if place_id not in place_scores:
                place_scores[place_id] = 0.0
                place_scores_by_category[place_id] = {}
            place_scores[place_id] += weighted_score
            place_scores_by_category[place_id][key] = round(weighted_score, 4)
    
    if not place_scores:
        # 카테고리가 하나도 없을 때: 위치 필터가 있으면 반경 내 장소를 거리순으로 반환 (ai_score=0)
        if location_filter and candidate_place_ids:
            from math import radians, cos, sin, asin, sqrt
            R = 6371.0
            lat = location_filter["latitude"]
            lon = location_filter["longitude"]
            lat1, lon1 = radians(lat), radians(lon)
            places_in_radius = db.execute(select(Place).where(Place.id.in_(candidate_place_ids))).scalars().all()
            with_distance = []
            for p in places_in_radius:
                lat2, lon2 = radians(p.latitude), radians(p.longitude)
                dlat = lat2 - lat1
                dlon = lon2 - lon1
                a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
                c = 2 * asin(sqrt(a))
                with_distance.append((p, R * c))
            with_distance.sort(key=lambda x: x[1])
            top_places = [p for p, _ in with_distance[:limit]]
            top_scores = {p.id: 0.0 for p in top_places}
            by_cat = {p.id: {} for p in top_places}
            return [PlaceOut.model_validate(p) for p in top_places], categories, top_scores, by_cat
        return [], categories, {}, {}
    
    sorted_place_ids = sorted(place_scores.items(), key=lambda x: x[1], reverse=True)[:limit]
    if not sorted_place_ids:
        return [], categories, {}, {}
    top_place_ids = [place_id for place_id, _ in sorted_place_ids]
    top_scores = {pid: score for pid, score in sorted_place_ids}
    # 메뉴가 지정된 경우: 메뉴 기여점수가 너무 낮은 장소는 제외 (짜장면 요청에 훠궈집이 나오는 것 방지)
    if menu_specified:
        filtered_ids = [
            pid for pid in top_place_ids
            if place_scores_by_category.get(pid, {}).get("menu", 0) >= MENU_MIN_WEIGHTED_SCORE
        ]
        if filtered_ids:
            order_map = {pid: i for i, pid in enumerate(top_place_ids)}
            filtered_ids.sort(key=lambda p: order_map.get(p, 9999))
            top_place_ids = filtered_ids[:limit]
            top_scores = {pid: top_scores[pid] for pid in top_place_ids if pid in top_scores}
        else:
            top_place_ids = []
            top_scores = {}
    places: Iterable[Place] = (
        db.execute(select(Place).where(Place.id.in_(top_place_ids))).scalars().all()
    )
    ordered_places = sorted(places, key=lambda p: top_place_ids.index(p.id))
    # 카테고리별 점수는 top_place_ids에 있는 것만 (나머지는 버림)
    top_by_cat = {pid: place_scores_by_category.get(pid, {}) for pid in top_place_ids}
    return [PlaceOut.model_validate(p) for p in ordered_places], categories, top_scores, top_by_cat


