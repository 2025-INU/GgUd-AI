"""
DB에 저장된 장소들에 대해 리뷰 크롤링 후 요약/임베딩 저장
------------------------------------------
1. DB에서 모든 장소 ID 가져오기
2. 각 장소에 대해 리뷰 크롤링
3. 리뷰 원본은 S3에만 저장 (선택)
4. 장소별 리뷰를 1개 요약으로 압축하고, 요약문은 reviews/임베딩은 place_summary_embeddings에 저장
"""

from __future__ import annotations

import argparse
import asyncio
import os
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
from app.services.recommendation import refresh_place_summary_embeddings_from_review_texts

# S3 (선택): .env에 S3_BUCKET_NAME 있으면 리뷰 업로드
try:
    from utils.s3_storage import S3StorageManager
    S3_AVAILABLE = True
except Exception:
    S3StorageManager = None
    S3_AVAILABLE = False

# review_crawl.py의 크롤러 import (같은 scripts 폴더 안에 있음)
# scripts 폴더를 sys.path에 추가
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from review_crawl import NaverMapReviewCrawler
async def crawl_reviews_for_place(
    crawler: NaverMapReviewCrawler,
    db: Session,
    place_id: int,
    max_count: int = 100,
) -> tuple[int, int, int, list[dict]]:
    """특정 장소 리뷰를 크롤링해 요약 임베딩 생성. 반환: (리뷰수, 임베딩수, 실패수, 원본리뷰)."""
    
    try:
        reviews = await crawler.crawl_all_reviews(str(place_id), set(), max_count=max_count)
    except Exception as exc:
        print(f"[ERROR] place_id={place_id} 크롤링 실패: {exc}", file=sys.stderr)
        import traceback
        if len(str(exc)) < 200:  # 짧은 에러만 상세 출력
            print(traceback.format_exc(), file=sys.stderr)
        return 0, 0, 1, []

    if not reviews:
        return 0, 0, 0, []

    review_texts = [
        (review_data.get("content") or "").strip()
        for review_data in reviews
        if (review_data.get("content") or "").strip()
    ]
    if not review_texts:
        return 0, 0, 0, reviews

    place_name = db.query(Place.name).filter(Place.id == place_id).scalar()
    try:
        _, _, inserted = refresh_place_summary_embeddings_from_review_texts(
            db=db,
            place_id=place_id,
            review_texts=review_texts,
            place_name=place_name,
        )
    except Exception as exc:
        db.rollback()
        print(f"[FAIL] place_id={place_id} 요약 임베딩 저장 실패: {exc}", file=sys.stderr)
        return len(review_texts), 0, 1, reviews

    return len(review_texts), inserted, 0, reviews


async def crawl_reviews_from_db(
    place_ids: list[int] | None = None,
    max_count: int = 100,
    limit: int | None = None,
    headless: bool = True,
) -> None:
    """DB에 저장된 장소들에 대해 리뷰 크롤링 후 요약 임베딩 저장."""
    db = SessionLocal()
    try:
        # 장소 목록 가져오기
        if place_ids:
            target_ids = list(dict.fromkeys(place_ids))
        else:
            places = db.query(Place.id).all()
            target_ids = [p.id for p in places]

        if limit:
            target_ids = target_ids[:limit]

        print(f"📋 총 {len(target_ids)}개 장소에 대해 리뷰 크롤링 시작...")

        s3_manager = None
        if S3_AVAILABLE and os.getenv("S3_BUCKET_NAME"):
            try:
                s3_manager = S3StorageManager(
                    bucket_name=os.getenv("S3_BUCKET_NAME"),
                    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                    region=os.getenv("AWS_REGION", "ap-northeast-2"),
                )
                print("S3 업로드 활성화 (리뷰 원본 저장)", file=sys.stderr)
            except Exception as e:
                print(f"S3 초기화 실패: {e}", file=sys.stderr)

        # 크롤러 초기화
        crawler = NaverMapReviewCrawler(headless=headless, verbose=True)

        total_reviews = 0
        total_embeddings = 0
        total_failed = 0
        places_processed = 0

        for i, place_id in enumerate(target_ids, 1):
            print(f"\n[{i}/{len(target_ids)}] place_id={place_id} 처리 중...", file=sys.stderr)
            
            review_count, embeddings_created, failed, reviews_raw = await crawl_reviews_for_place(crawler, db, place_id, max_count)
            
            if review_count > 0:
                places_processed += 1
                total_reviews += review_count
                total_embeddings += embeddings_created
                total_failed += failed
                print(
                    f"[INFO] place_id={place_id}: 리뷰 {review_count}개 처리, 요약 임베딩 {embeddings_created}개 저장",
                    file=sys.stderr,
                )
                # S3 업로드 (버킷 설정 시)
                if s3_manager and reviews_raw:
                    try:
                        s3_manager.upload_reviews(str(place_id), reviews_raw)
                        print(f"[INFO] place_id={place_id}: S3 업로드 완료", file=sys.stderr)
                    except Exception as e:
                        print(f"[WARN] place_id={place_id} S3 업로드 실패: {e}", file=sys.stderr)
            else:
                print(f"[INFO] place_id={place_id}: 리뷰 없음 또는 요약 생성 실패", file=sys.stderr)

        print("\n" + "=" * 60)
        print("리뷰 크롤링 및 요약 임베딩 저장 완료")
        print("=" * 60)
        print(f"  처리된 장소: {places_processed}개")
        print(f"  처리된 리뷰 텍스트: {total_reviews}개")
        print(f"  저장된 요약 임베딩: {total_embeddings}개")
        print(f"  실패 건수: {total_failed}개")
        print("=" * 60)

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="리뷰를 S3에 저장하고 장소 요약 임베딩을 DB에 저장")
    parser.add_argument(
        "--place-ids",
        type=str,
        help="처리할 place_id 목록 (쉼표로 구분, 예: 123,456,789)",
    )
    parser.add_argument(
        "--max-count",
        type=int,
        default=100,
        help="장소별 최대 리뷰 수집 개수 (기본: 100)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="처리할 장소 최대 개수 (테스트용)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="브라우저를 헤드리스 모드로 실행 (기본: True)",
    )
    args = parser.parse_args()

    place_ids = None
    if args.place_ids:
        try:
            place_ids = [int(x.strip()) for x in args.place_ids.split(",") if x.strip()]
        except ValueError:
            raise SystemExit("--place-ids는 숫자로만 구성되어야 합니다.")

    asyncio.run(
        crawl_reviews_from_db(
            place_ids=place_ids,
            max_count=args.max_count,
            limit=args.limit,
            headless=args.headless,
        )
    )


if __name__ == "__main__":
    main()
