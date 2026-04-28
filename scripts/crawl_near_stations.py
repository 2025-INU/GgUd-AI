"""
지하철역 기준으로 역별 "역명 식당", "역명 카페", "역명 술집" 자동 크롤링.

플로우: 장소 크롤링 → DB 적재 → 리뷰 크롤링 → 요약 저장 + 요약 임베딩 저장

사용법:
  # 기본: 역 전체 대상 크롤링 + DB 적재 + 리뷰 + 임베딩
  python scripts/crawl_near_stations.py
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from sqlalchemy import create_engine, text

# GgUd-AI 프로젝트 루트
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
# Backend CSV (ggud_local/Backend/...)
BACKEND_CSV = BACKEND_ROOT.parent / "Backend" / "src" / "main" / "resources" / "data" / "seoul_subway_stations.csv"
CRAWL_KEYWORDS: tuple[str, ...] = ("식당", "카페", "술집")
LIMIT_PER_QUERY = 100
MAX_REVIEWS_PER_PLACE = 100
THUMBNAIL_ONLY = True

# app 모듈 import를 위해 경로 추가
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.session import SessionLocal
from app.services.crawl_runner import ingest_from_crawl


def normalize_station_query_name(station_name: str) -> str:
    """검색 쿼리용 역명 정규화: '서울' -> '서울역', 이미 '역'이면 그대로."""
    name = (station_name or "").strip()
    if not name:
        return name
    if name.endswith("역"):
        return name
    return f"{name}역"


def load_station_names(csv_path: Path, encoding: str = "euc-kr") -> list[str]:
    """CSV에서 역명(컬럼 인덱스 3)만 추출, 중복 제거 순서 유지."""
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV를 찾을 수 없습니다: {csv_path}")
    names: list[str] = []
    seen: set[str] = set()
    with csv_path.open("r", encoding=encoding, errors="replace") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 4:
                continue
            if i == 0 and parts[0].isdigit() is False:
                continue  # 헤더 스킵 (숫자로 안 시작하면 헤더로 간주)
            name = parts[3]
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def load_station_names_from_backend_db() -> list[str]:
    """Backend DB의 subway_stations 테이블에서 역명 목록 조회."""
    backend_db_url = os.getenv("BACKEND_DATABASE_URL")
    if not backend_db_url:
        host = os.getenv("BACKEND_DB_HOST", "127.0.0.1")
        port = os.getenv("BACKEND_DB_PORT", "5432")
        user = os.getenv("BACKEND_DB_USER", "ggud_user")
        password = os.getenv("BACKEND_DB_PASSWORD", "ggud_db_pw")
        dbname = os.getenv("BACKEND_DB_NAME", "ggud_db")
        backend_db_url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{dbname}"

    engine = create_engine(backend_db_url)
    sql = text(
        """
        SELECT DISTINCT station_name
        FROM subway_stations
        WHERE station_name IS NOT NULL
          AND station_name <> ''
        ORDER BY station_name
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [row[0] for row in rows if row[0]]


def run_crawl_reviews(max_reviews: int = 20, limit_places: int | None = None) -> bool:
    """crawl_reviews_from_db.py 실행. 리뷰 크롤링 + 요약 저장 + 요약 임베딩 저장."""
    cmd = [
        sys.executable,
        str(BACKEND_ROOT / "scripts" / "crawl_reviews_from_db.py"),
        "--max-count", str(max_reviews),
    ]
    if limit_places is not None:
        cmd.extend(["--limit", str(limit_places)])
    print("\n[리뷰 크롤링] DB 장소별 네이버 리뷰 수집", flush=True)
    result = subprocess.run(cmd, cwd=str(BACKEND_ROOT))
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="지하철역 기준 역별 식당/카페/술집 자동 크롤링",
    )
    parser.add_argument(
        "--max-stations",
        type=int,
        default=None,
        help="처리할 역 개수 제한 (테스트용, 기본: 전부)",
    )
    args = parser.parse_args()

    stations: list[str] = []
    try:
        stations = load_station_names_from_backend_db()
        print(f"역명 소스: Backend DB ({len(stations)}개)", file=sys.stderr)
    except Exception as exc:
        print(f"[WARN] Backend DB 역명 조회 실패: {exc}", file=sys.stderr)
        print("[WARN] CSV 소스로 fallback합니다.", file=sys.stderr)
        stations = load_station_names(BACKEND_CSV)

    if not stations:
        print("역 목록이 비어 있습니다.", file=sys.stderr)
        sys.exit(1)

    if args.max_stations is not None:
        stations = stations[: args.max_stations]

    print(f"총 {len(stations)}개 역 대상 (역당 식당/카페/술집 각 {LIMIT_PER_QUERY}개)")
    print("=" * 60)
    total_ingested = 0
    total_skipped = 0

    db = SessionLocal()
    try:
        for i, name in enumerate(stations, 1):
            print(f"[{i}/{len(stations)}] {name}")
            station_query_name = normalize_station_query_name(name)
            for keyword in CRAWL_KEYWORDS:
                query = f"{station_query_name} {keyword}"
                summary = ingest_from_crawl(
                    db,
                    query,
                    thumbnail_only=THUMBNAIL_ONLY,
                    limit=LIMIT_PER_QUERY,
                )
                total_ingested += summary.places_fetched
                total_skipped += summary.places_skipped
                print(
                    f"  [DB upsert] {query} -> 신규 {summary.places_fetched}개, 스킵 {summary.places_skipped}개",
                    flush=True,
                )
    finally:
        db.close()

    print("=" * 60)
    print(f"크롤링+DB upsert 완료. 신규 {total_ingested}개, 스킵 {total_skipped}개")
    run_crawl_reviews(max_reviews=MAX_REVIEWS_PER_PLACE, limit_places=None)


if __name__ == "__main__":
    main()
