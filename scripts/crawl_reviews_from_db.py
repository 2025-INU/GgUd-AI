"""
DB에 저장된 장소들에 대해 리뷰 크롤링 및 저장
------------------------------------------
1. DB에서 모든 장소 ID 가져오기
2. 각 장소에 대해 리뷰 크롤링
3. 크롤링한 리뷰를 DB에 저장
4. (선택) .env에 S3_BUCKET_NAME 있으면 장소별 리뷰를 S3에 업로드 (reviews/{place_id}/reviews.json)
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
from app.models.review import Review

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
from datetime import datetime


def parse_visit_date(date_str: str | None) -> datetime | None:
    """방문 날짜 문자열을 datetime으로 변환."""
    if not date_str:
        return None
    # "1.24.토" 형식 처리
    try:
        parts = date_str.split(".")
        if len(parts) >= 2:
            month = int(parts[0])
            day = int(parts[1])
            # 현재 연도 사용 (정확하지 않지만 크롤링 시점 기준)
            year = datetime.now().year
            return datetime(year, month, day)
    except (ValueError, IndexError):
        pass
    return None


def upsert_review_to_db(db: Session, place_id: int, review_data: dict) -> Review | None:
    """리뷰 데이터를 DB에 저장 또는 업데이트."""
    review_id_str = review_data.get("id") or review_data.get("review_id")
    if not review_id_str:
        return None

    try:
        # 기존 리뷰 확인
        existing = db.query(Review).filter(Review.review_id == review_id_str).first()
        
        visit_date = None
        if review_data.get("visit_date"):
            # review_crawl.py의 parse_visit_date 사용
            visit_date = parse_visit_date(review_data.get("visit_date"))
        
        crawled_at = datetime.now()

        if existing:
            # 업데이트
            existing.content = review_data.get("content", "")
            existing.author = review_data.get("author")
            existing.rating = review_data.get("rating")
            existing.visit_date = visit_date
            existing.crawled_at = crawled_at
            db.commit()
            db.refresh(existing)
            return existing
        else:
            # 새로 생성
            review = Review(
                place_id=place_id,
                review_id=review_id_str,
                author=review_data.get("author"),
                content=review_data.get("content", ""),
                rating=review_data.get("rating"),
                visit_date=visit_date,
                crawled_at=crawled_at,
            )
            db.add(review)
            db.commit()
            db.refresh(review)
            return review
    except Exception:
        db.rollback()
        raise


async def crawl_reviews_for_place(
    crawler: NaverMapReviewCrawler,
    db: Session,
    place_id: int,
    max_count: int = 100,
) -> tuple[int, int, list[dict]]:
    """특정 장소에 대해 리뷰를 크롤링하고 DB에 저장. 반환: (성공 수, 실패 수, 크롤된 리뷰 원본 리스트)."""
    # DB에 이미 저장된 리뷰 ID 목록 가져오기 (중복 방지)
    existing_review_ids = {
        r.review_id for r in db.query(Review.review_id).filter(Review.place_id == place_id).all()
    }
    
    try:
        reviews = await crawler.crawl_all_reviews(str(place_id), existing_review_ids, max_count=max_count)
    except Exception as exc:
        print(f"[ERROR] place_id={place_id} 크롤링 실패: {exc}", file=sys.stderr)
        import traceback
        if len(str(exc)) < 200:  # 짧은 에러만 상세 출력
            print(traceback.format_exc(), file=sys.stderr)
        return 0, 0, []

    if not reviews:
        return 0, 0, []

    success = 0
    failed = 0

    for review_data in reviews:
        content = review_data.get("content", "").strip()
        if not content:
            continue

        try:
            upsert_review_to_db(db, place_id, review_data)
            success += 1
        except Exception as exc:
            failed += 1
            if failed <= 3:
                print(f"[FAIL] place_id={place_id}, review_id={review_data.get('id')} 저장 실패: {exc}", file=sys.stderr)

    return success, failed, reviews


async def crawl_reviews_from_db(
    place_ids: list[int] | None = None,
    max_count: int = 100,
    limit: int | None = None,
    headless: bool = True,
) -> None:
    """DB에 저장된 장소들에 대해 리뷰 크롤링 및 저장."""
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
                print("S3 업로드 활성화 (리뷰 저장 시 함께 업로드)", file=sys.stderr)
            except Exception as e:
                print(f"S3 초기화 실패 (리뷰는 DB만 저장): {e}", file=sys.stderr)

        # 크롤러 초기화
        crawler = NaverMapReviewCrawler(headless=headless, verbose=True)

        total_success = 0
        total_failed = 0
        places_processed = 0

        for i, place_id in enumerate(target_ids, 1):
            print(f"\n[{i}/{len(target_ids)}] place_id={place_id} 처리 중...", file=sys.stderr)
            
            success, failed, reviews_raw = await crawl_reviews_for_place(crawler, db, place_id, max_count)
            
            if success > 0:
                places_processed += 1
                total_success += success
                total_failed += failed
                print(f"[INFO] place_id={place_id}: {success}개 리뷰 저장 완료", file=sys.stderr)
                # S3 업로드 (버킷 설정 시)
                if s3_manager and reviews_raw:
                    try:
                        s3_manager.upload_reviews(str(place_id), reviews_raw)
                        print(f"[INFO] place_id={place_id}: S3 업로드 완료", file=sys.stderr)
                    except Exception as e:
                        print(f"[WARN] place_id={place_id} S3 업로드 실패: {e}", file=sys.stderr)
            else:
                print(f"[INFO] place_id={place_id}: 리뷰 없음 또는 크롤링 실패", file=sys.stderr)

        print("\n" + "=" * 60)
        print("리뷰 크롤링 및 저장 완료")
        print("=" * 60)
        print(f"  처리된 장소: {places_processed}개")
        print(f"  저장된 리뷰: {total_success}개")
        print(f"  실패한 리뷰: {total_failed}개")
        print("=" * 60)

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="DB에 저장된 장소들에 대해 리뷰 크롤링 및 저장")
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
