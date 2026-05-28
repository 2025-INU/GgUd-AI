"""추천 품질 테스트: 다양한 쿼리에 대해 카테고리 추출과 추천 결과를 한 번에 확인."""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_ROOT / ".env")
sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal  # noqa: E402
from app.models.place import Place  # noqa: E402
from app.services.llm import llm_service  # noqa: E402
from app.services.recommendation import recommend_places  # noqa: E402


# (쿼리, 기대하는 분류, 비고)
TEST_QUERIES = [
    ("떡볶이 집 추천해줘", "분식/떡볶이", ""),
    ("스터디 카페 어디 있어", "스터디카페", ""),
    ("파스타 맛집 데이트", "양식/이탈리아음식", ""),
    ("핫한 술집", "술집/맥주,호프", "인기도 폴백"),
    ("이태원 와인바", "와인/바(BAR)", "위치+업종"),
]


def run_one(query: str, expected: str, note: str) -> None:
    print("=" * 88)
    print(f"❓ 쿼리: {query}")
    if expected:
        print(f"   기대 place_type: {expected}    {note}")
    print("-" * 88)
    try:
        cats = llm_service.extract_categories_from_query(query)
    except Exception as exc:
        print(f"[FAIL] extract_categories_from_query: {exc}")
        return
    print(
        f"  추출: companion={cats.companion!r} menu={cats.menu!r} "
        f"mood={cats.mood!r} purpose={cats.purpose!r} place_type={cats.place_type!r}"
    )

    location = None
    try:
        location = llm_service.extract_location_from_query(query)
    except Exception as exc:
        print(f"  [warn] location 추출 실패: {exc}")
    if location:
        print(f"  위치: lat={location['latitude']:.4f}, lon={location['longitude']:.4f}")

    db = SessionLocal()
    try:
        location_filter = None
        if location:
            location_filter = {
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "radius_km": 2.0,
            }
        items, _extracted, scores, by_cat = recommend_places(
            db, cats, limit=5, location_filter=location_filter, tab=None
        )
        rc_map = {}
        if items:
            ids = [p.id for p in items]
            rows = db.query(Place.id, Place.review_count).filter(Place.id.in_(ids)).all()
            rc_map = {r[0]: (r[1] or 0) for r in rows}
    except Exception as exc:
        print(f"[FAIL] recommend_places: {exc}")
        return
    finally:
        db.close()

    if not items:
        print("  ❌ 추천 결과 없음")
        return
    print(f"  ✅ 추천 {len(items)}건")
    for i, p in enumerate(items, 1):
        raw = scores.get(p.id, 0.0)
        bc = by_cat.get(p.id, {})
        bc_str = " ".join(f"{k}={v}" for k, v in bc.items())
        rc = rc_map.get(p.id, 0)
        print(
            f"   [{i}] score={raw:.4f}  rc={rc:>5}  "
            f"{p.name}  ({p.category})  {bc_str}"
        )


def main() -> None:
    print(f"\n총 {len(TEST_QUERIES)}개 쿼리 테스트\n")
    for q, exp, note in TEST_QUERIES:
        run_one(q, exp, note)
    print("=" * 88)
    print("✅ 전체 테스트 완료")


if __name__ == "__main__":
    main()
