"""
임베딩 생성 스크립트
------------------
DB에 저장된 리뷰에서 카테고리 정보를 추출하고 PlaceEmbedding을 생성합니다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 환경 변수 로드
from dotenv import load_dotenv

# backend 폴더의 .env 파일 로드
BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_ROOT / ".env")

# backend 모듈 import를 위해 경로 추가
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.place import Place
from app.models.review import Review
from app.services.recommendation import refresh_embeddings


def generate_embeddings_for_place(db: Session, place_id: int) -> tuple[int, int]:
    """특정 장소의 모든 리뷰에서 임베딩 생성."""
    place = db.get(Place, place_id)
    if not place:
        return 0, 0

    reviews = db.query(Review).filter(Review.place_id == place_id).all()
    if not reviews:
        return 0, 0

    total_inserted = 0
    processed = 0

    for review in reviews:
        content = review.content.strip()
        if not content:
            continue

        try:
            _, inserted = refresh_embeddings(db, place_id, review.id, content)
            total_inserted += inserted
            processed += 1
        except Exception as exc:
            print(f"[ERROR] place_id={place_id}, review_id={review.id} error={exc}", file=sys.stderr)

    return processed, total_inserted


def generate_embeddings(
    db: Session,
    place_ids: list[int] | None = None,
    limit: int | None = None,
) -> tuple[int, int, int]:
    """리뷰에서 임베딩 생성."""
    if place_ids:
        target_ids = list(dict.fromkeys(place_ids))
    else:
        # 모든 장소 가져오기
        places = db.query(Place.id).all()
        target_ids = [p.id for p in places]

    if limit:
        target_ids = target_ids[:limit]

    places_processed = 0
    reviews_processed = 0
    embeddings_created = 0

    for i, place_id in enumerate(target_ids, 1):
        print(f"[{i}/{len(target_ids)}] place_id={place_id} 처리 중...", end=" ", flush=True, file=sys.stderr)
        
        processed, inserted = generate_embeddings_for_place(db, place_id)
        
        if processed > 0:
            places_processed += 1
            reviews_processed += processed
            embeddings_created += inserted
            print(f"✅ {processed}개 리뷰 처리, {inserted}개 임베딩 생성", file=sys.stderr)
        else:
            print(f"⏭️  리뷰 없음", file=sys.stderr)

    return places_processed, reviews_processed, embeddings_created


def main() -> None:
    parser = argparse.ArgumentParser(description="리뷰에서 카테고리 임베딩 생성")
    parser.add_argument(
        "--place-ids",
        type=str,
        help="처리할 place_id 목록 (쉼표로 구분, 예: 123,456,789)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="처리할 장소 최대 개수 (테스트용)",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        place_ids = None
        if args.place_ids:
            try:
                place_ids = [int(x.strip()) for x in args.place_ids.split(",") if x.strip()]
            except ValueError:
                raise SystemExit("--place-ids는 숫자로만 구성되어야 합니다.")

        print("🚀 임베딩 생성 시작...")
        places_processed, reviews_processed, embeddings_created = generate_embeddings(
            db, place_ids, args.limit
        )

        print("\n" + "=" * 60)
        print("임베딩 생성 완료")
        print("=" * 60)
        print(f"  처리된 장소: {places_processed}개")
        print(f"  처리된 리뷰: {reviews_processed}개")
        print(f"  생성된 임베딩: {embeddings_created}개")
        print("=" * 60)
    finally:
        db.close()


if __name__ == "__main__":
    main()
