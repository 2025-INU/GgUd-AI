"""
일일 전체 파이프라인 실행 스크립트
--------------------------------
1) 장소 크롤링 (서울 주요 역 × 식당/카페/술집)
2) 리뷰 크롤링 (DB에 저장된 모든 장소 대상, 기존 review_id는 스킵)
3) 리뷰 임베딩 생성 (모든 리뷰 대상, refresh_embeddings가 중복 임베딩은 건너뜀)

배포 서버에서 하루 한 번만 이 스크립트를 실행하도록 cron 등에 등록하면 됩니다.

예시:
  python scripts/daily_pipeline.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

# backend 루트 추가
BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_ROOT / ".env")
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# scripts 폴더 추가 (crawl_reviews_from_db, generate_embeddings import 용)
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.db.session import SessionLocal

from daily_crawl_places import run_daily_crawl
from crawl_reviews_from_db import crawl_reviews_from_db
from generate_embeddings import generate_embeddings


def main() -> None:
    # 1) 장소 크롤링
    print("=== [1/3] 일일 장소 크롤링 시작 ===")
    run_daily_crawl()
    print("=== [1/3] 일일 장소 크롤링 완료 ===\n")

    # 2) 리뷰 크롤링 (DB에 있는 모든 place_id 대상, 기존 review_id는 내부에서 스킵)
    print("=== [2/3] 리뷰 크롤링 시작 ===")
    asyncio.run(
        crawl_reviews_from_db(
            place_ids=None,  # 모든 장소
            max_count=100,   # 장소당 최대 리뷰 수집 개수 (필요 시 조정)
            limit=None,
            headless=True,
        )
    )
    print("=== [2/3] 리뷰 크롤링 완료 ===\n")

    # 3) 리뷰 임베딩 생성
    print("=== [3/3] 리뷰 임베딩 생성 시작 ===")
    db = SessionLocal()
    try:
        places_processed, reviews_processed, embeddings_created = generate_embeddings(
            db, place_ids=None, limit=None
        )
        print("\n임베딩 생성 요약:")
        print(f"  처리된 장소: {places_processed}개")
        print(f"  처리된 리뷰: {reviews_processed}개")
        print(f"  생성된 임베딩: {embeddings_created}개")
    finally:
        db.close()
    print("=== [3/3] 리뷰 임베딩 생성 완료 ===")


if __name__ == "__main__":
    main()

