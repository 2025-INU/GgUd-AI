"""
매일 실행하는 장소 크롤링 배치
---------------------------
- 여러 검색 쿼리(예: '강남 카페', '홍대 맛집' 등)를 순회하면서
  naver_crawl.py를 통해 장소를 크롤링하고 DB에 upsert 합니다.

사용 예:
  python scripts/daily_crawl_places.py

cron 예시:
  0 4 * * * cd /opt/ggud/GgUd-AI && .venv/bin/python scripts/daily_crawl_places.py >> /opt/ggud/logs/daily_crawl_places.log 2>&1
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

# backend 루트 추가
BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_ROOT / ".env")
sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal
from app.services.crawl_runner import ingest_from_crawl


# 서울 주요 지하철역 + 업종(식당/카페/술집) 조합
SEOUL_STATIONS: list[str] = [
    "강남역",
    "역삼역",
    "선릉역",
    "잠실역",
    "건대입구역",
    "홍대입구역",
    "신촌역",
    "합정역",
    "이태원역",
]

CATEGORIES: list[str] = ["식당", "카페", "술집"]


def build_default_queries() -> list[str]:
    return [f"{station} {cat}" for station in SEOUL_STATIONS for cat in CATEGORIES]


def run_daily_crawl(queries: list[str] | None = None) -> None:
    """여러 검색 쿼리에 대해 장소 크롤링 + DB upsert."""
    if not queries:
        queries = build_default_queries()

    db = SessionLocal()
    try:
        total_ingested = 0
        total_skipped = 0

        for i, q in enumerate(queries, 1):
            print(f"\n[{i}/{len(queries)}] 쿼리='{q}' 크롤링 시작...", file=sys.stderr)
            try:
                summary = ingest_from_crawl(db, q)
                total_ingested += summary.places_fetched
                total_skipped += summary.places_skipped
                print(
                    f"[INFO] 쿼리='{q}' → 신규 {summary.places_fetched}개, 스킵 {summary.places_skipped}개",
                    file=sys.stderr,
                )
            except Exception as exc:
                print(f"[ERROR] 쿼리='{q}' 크롤링 실패: {exc}", file=sys.stderr)

        print("\n" + "=" * 60)
        print("일일 장소 크롤링 완료")
        print("=" * 60)
        print(f"  신규 장소: {total_ingested}개")
        print(f"  이미 존재해서 스킵: {total_skipped}개")
        print("=" * 60)
    finally:
        db.close()


if __name__ == "__main__":
    run_daily_crawl()

