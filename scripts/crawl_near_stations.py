"""
지하철역 기준으로 역별 "역명 식당", "역명 카페", "역명 술집" 자동 크롤링.

플로우: 장소 크롤링 → AI DB 적재 → (선택) 리뷰 크롤링 → 요약/임베딩

내부적으로 scripts/naver_crawl_xy.py 의 NaverMapRestaurantCrawler 를 직접 사용한다.
(map.naver.com iframe + script JSON 파싱 방식이라 역당 60~90개 풀데이터 확보 가능)

사용법:
  # 기본: 역 전체 대상 크롤링 + DB 적재 + 리뷰 + 임베딩
  python scripts/crawl_near_stations.py

  # 처음 5개 역만, 리뷰 생략
  python scripts/crawl_near_stations.py --max-stations 5 --skip-reviews
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from dotenv import load_dotenv


SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
load_dotenv(BACKEND_ROOT / ".env")

BACKEND_CSV = (
    BACKEND_ROOT.parent
    / "Backend"
    / "src"
    / "main"
    / "resources"
    / "data"
    / "seoul_subway_stations.csv"
)
CRAWL_KEYWORDS: tuple[str, ...] = ("식당", "카페", "술집")
LIMIT_PER_QUERY = 100
MAX_REVIEWS_PER_PLACE = 100
DEFAULT_PROGRESS_PATH = SCRIPT_DIR / ".crawl_progress.json"

# 차단 회피용 대기 (초)
SLEEP_BETWEEN_KEYWORDS = 2.0
SLEEP_BETWEEN_STATIONS = 3.0
SLEEP_AFTER_BLOCK = 60.0


def load_progress(path: Path) -> set[str]:
    """이미 처리 끝난 역 이름 set 을 읽어온다."""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        completed = data.get("completed_stations") or []
        return {str(name) for name in completed}
    except Exception as e:
        print(f"[WARN] 진행상황 파일 읽기 실패({path}): {e} -- 처음부터 시작합니다", file=sys.stderr)
        return set()


def save_progress(path: Path, completed: set[str]) -> None:
    """완료된 역 set 을 파일에 덤프 (atomic write)."""
    payload = {
        "completed_stations": sorted(completed),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from app.db.session import SessionLocal
from app.models.place import Place
from app.services.recommendation import upsert_place
from naver_crawl_xy import NaverMapRestaurantCrawler


def normalize_station_query_name(station_name: str) -> str:
    """검색 쿼리용 역명 정규화.

    - 괄호 부속명 제거: '석남(거북시장)' -> '석남', '가정(루원시티)' -> '가정'
    - '역' 자 자동 부착: '서울' -> '서울역', 이미 '역'이면 그대로
    """
    name = (station_name or "").strip()
    if not name:
        return name
    paren_idx = name.find("(")
    if paren_idx > 0:
        name = name[:paren_idx].strip()
    if name.endswith("역"):
        return name
    return f"{name}역"


def resolve_station_names(
    requested: list[str], available: list[str]
) -> tuple[list[str], list[str]]:
    """사용자가 입력한 역명을 DB 역명에 유연하게 매칭.

    매칭 우선순위:
      1) 완전 일치
      2) "<input>(...)" 형식 - 부속명이 괄호로 붙는 케이스 (석남 -> 석남(거북시장))
      3) "<input>역" 또는 "<input>역(...)" 케이스 (혹시 모를 표기 차이 대비)
      4) 부분 일치 (substring)

    여러 후보가 매칭되면 모두 포함, 못 찾은 입력은 missing 으로 반환.
    """
    available_set = set(available)
    resolved: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        if name not in seen:
            seen.add(name)
            resolved.append(name)

    for raw in requested:
        q = raw.strip()
        if not q:
            continue

        if q in available_set:
            add(q)
            continue

        prefix_paren = [s for s in available if s.startswith(q + "(")]
        if prefix_paren:
            for s in prefix_paren:
                add(s)
            continue

        with_yeok = [
            s for s in available if s == f"{q}역" or s.startswith(f"{q}역(")
        ]
        if with_yeok:
            for s in with_yeok:
                add(s)
            continue

        contains = [s for s in available if q in s]
        if contains:
            for s in contains:
                add(s)
            continue

        missing.append(q)

    return resolved, missing


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
                continue
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


def _build_existing_index(db) -> dict[tuple[str, float, float], int]:
    """이미 DB에 있는 (name, lat5, lon5) -> place_id 인덱스를 한 번에 만들어 반환.

    네이버가 같은 가게에 다른 place_id 를 주는 경우(예: 3539026 / 12950401 / 11555415)
    가 잦아서 place_id 만으로는 중복을 막을 수 없다. upsert_places 시작 시점에 한 번
    스캔해 메모리 set 으로 들고 다닌다.
    """
    rows = db.query(Place.id, Place.name, Place.latitude, Place.longitude).all()
    index: dict[tuple[str, float, float], int] = {}
    for row_id, name, lat, lon in rows:
        if name is None or lat is None or lon is None:
            continue
        key = (name.strip(), round(float(lat), 5), round(float(lon), 5))
        # 기존 행이 여러 개라도 첫 번째 하나만 유지 (어차피 다른 행은 skip 대상)
        index.setdefault(key, int(row_id))
    return index


def upsert_places(db, places: list[dict]) -> tuple[int, int]:
    """크롤 결과를 AI DB places 테이블에 upsert. (신규, 스킵) 카운트 반환."""
    ingested = 0
    skipped = 0
    existing_index = _build_existing_index(db)
    for place in places:
        place_id_raw = place.get("place_id")
        if not place_id_raw:
            skipped += 1
            continue
        try:
            place_id = int(place_id_raw)
        except (ValueError, TypeError):
            skipped += 1
            continue

        road_address = place.get("road_address") or place.get("address") or place.get("origin_address")
        if not road_address:
            skipped += 1
            continue
        latitude = place.get("latitude")
        longitude = place.get("longitude")
        if latitude is None or longitude is None:
            skipped += 1
            continue

        # 이미 있고 ai_summary/image_url 둘 다 채워진 항목은 스킵
        existing = db.get(Place, place_id)
        if existing and existing.image_url and existing.ai_summary:
            skipped += 1
            continue

        # 같은 (name, lat5, lon5) 가 다른 place_id 로 이미 들어가 있으면 신규 INSERT 안 함.
        # 단, 같은 place_id 로 들어와서 보강(UPDATE) 하는 케이스는 통과시켜야 함.
        name_clean = (place.get("name") or "").strip()
        dup_key = (name_clean, round(float(latitude), 5), round(float(longitude), 5))
        dup_existing_id = existing_index.get(dup_key)
        if dup_existing_id is not None and dup_existing_id != place_id:
            skipped += 1
            continue

        payload = {
            "id": place_id,
            "name": name_clean,
            "category": place.get("category") or "기타",
            "road_address": road_address,
            "image_url": (place.get("image_url") or "").strip() or None,
            "ai_summary": (place.get("ai_summary") or "").strip() or None,
            "latitude": float(latitude),
            "longitude": float(longitude),
            "review_count": place.get("review_count") if place.get("review_count") is not None else 0,
        }
        try:
            upsert_place(db, payload)
            ingested += 1
            existing_index.setdefault(dup_key, place_id)
        except Exception as e:
            print(f"  [WARN] upsert 실패 place_id={place_id}: {e}", file=sys.stderr)
            skipped += 1
    return ingested, skipped


async def crawl_query(crawler: NaverMapRestaurantCrawler, query: str, limit: int) -> list[dict]:
    """단일 쿼리 크롤 (limit 만큼 슬라이스)."""
    try:
        results = await crawler.crawl_single_page(query, 1)
    except Exception as e:
        print(f"  [WARN] 크롤 실패: {e}", file=sys.stderr)
        return []
    if limit and len(results) > limit:
        results = results[:limit]
    return results


def run_crawl_reviews(max_reviews: int = 20, limit_places: int | None = None) -> bool:
    """crawl_reviews_from_db.py 실행. 리뷰 크롤링 + 요약 저장 + 요약 임베딩 저장."""
    cmd = [
        sys.executable,
        str(BACKEND_ROOT / "scripts" / "crawl_reviews_from_db.py"),
        "--max-count",
        str(max_reviews),
    ]
    if limit_places is not None:
        cmd.extend(["--limit", str(limit_places)])
    print("\n[리뷰 크롤링] DB 장소별 네이버 리뷰 수집", flush=True)
    result = subprocess.run(cmd, cwd=str(BACKEND_ROOT))
    return result.returncode == 0


async def crawl_all_stations(
    stations: list[str],
    limit_per_query: int,
    headless: bool = True,
    progress_path: Path | None = None,
    completed: set[str] | None = None,
) -> tuple[int, int]:
    """역 × 키워드 전부 돌면서 AI DB 적재. (총 신규, 총 스킵) 반환.

    역 한 개를 모두 끝낼 때마다 progress_path 에 완료된 역명을 누적 저장한다.
    """
    crawler = NaverMapRestaurantCrawler(headless=headless, verbose=False)
    db = SessionLocal()
    total_ingested = 0
    total_skipped = 0
    completed = completed if completed is not None else set()
    try:
        for i, name in enumerate(stations, 1):
            print(f"[{i}/{len(stations)}] {name}")
            station_query_name = normalize_station_query_name(name)
            for j, keyword in enumerate(CRAWL_KEYWORDS):
                query = f"{station_query_name} {keyword}"
                results = await crawl_query(crawler, query, limit_per_query)
                if not results:
                    print(f"  {query} -> 0개 (차단/검색결과 없음 가능)", flush=True)
                    # 차단 의심 시 길게 대기
                    if i > 1 or j > 0:
                        time.sleep(SLEEP_AFTER_BLOCK)
                    continue
                ingested, skipped = upsert_places(db, results)
                total_ingested += ingested
                total_skipped += skipped
                print(
                    f"  {query} -> 크롤 {len(results)}개 / 신규 {ingested}, 스킵 {skipped}",
                    flush=True,
                )
                if j < len(CRAWL_KEYWORDS) - 1:
                    time.sleep(SLEEP_BETWEEN_KEYWORDS)
            # 역 1개 완료 -> 체크포인트 저장
            completed.add(name)
            if progress_path is not None:
                save_progress(progress_path, completed)
            if i < len(stations):
                time.sleep(SLEEP_BETWEEN_STATIONS)
    finally:
        db.close()
    return total_ingested, total_skipped


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
    parser.add_argument(
        "--skip-reviews",
        action="store_true",
        help="장소 크롤링·DB 적재만 하고 리뷰 크롤링은 생략",
    )
    parser.add_argument(
        "--limit-per-query",
        type=int,
        default=None,
        help=f"역×키워드당 최대 장소 수 (기본: {LIMIT_PER_QUERY})",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="브라우저를 화면에 표시 (디버그용)",
    )
    parser.add_argument(
        "--progress-file",
        type=str,
        default=str(DEFAULT_PROGRESS_PATH),
        help=f"체크포인트 파일 경로 (기본: {DEFAULT_PROGRESS_PATH})",
    )
    parser.add_argument(
        "--reset-progress",
        action="store_true",
        help="체크포인트 무시하고 처음부터 다시 (파일도 삭제)",
    )
    parser.add_argument(
        "--stations",
        type=str,
        default=None,
        help='특정 역만 크롤링 (콤마 구분, 예: "하남검단산,하남시청"). '
             "지정 시 체크포인트 무시하고 해당 역들만 강제 재크롤.",
    )
    args = parser.parse_args()
    limit_per_query = args.limit_per_query if args.limit_per_query is not None else LIMIT_PER_QUERY

    progress_path = Path(args.progress_file)
    if args.reset_progress and progress_path.exists():
        progress_path.unlink()
        print(f"[INFO] 체크포인트 초기화: {progress_path}", file=sys.stderr)
    completed = load_progress(progress_path)

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

    # --stations 지정 시 해당 역만 강제 재크롤 (체크포인트 / max-stations 무시)
    if args.stations:
        requested = [s.strip() for s in args.stations.split(",") if s.strip()]
        pending_stations, missing = resolve_station_names(requested, stations)
        if missing:
            print(
                f"[WARN] 다음 역명은 Backend DB/CSV 에 없습니다(무시): {', '.join(missing)}",
                file=sys.stderr,
            )
        if not pending_stations:
            print("크롤링할 유효한 역이 없습니다.", file=sys.stderr)
            sys.exit(1)
        # 강제 재크롤 -> 체크포인트에서 제거
        for s in pending_stations:
            completed.discard(s)
        print(
            f"[--stations] 지정된 {len(pending_stations)}개 역 강제 크롤: {pending_stations}",
            file=sys.stderr,
        )
    else:
        if args.max_stations is not None:
            stations = stations[: args.max_stations]
        # 체크포인트로 끝난 역은 skip
        pending_stations = [s for s in stations if s not in completed]
        skipped_done = len(stations) - len(pending_stations)
        if skipped_done > 0:
            print(
                f"[체크포인트] 이미 완료된 역 {skipped_done}개 건너뜁니다 ({progress_path})",
                file=sys.stderr,
            )

    if not pending_stations:
        print("모든 역이 이미 완료된 상태입니다. (--reset-progress 로 다시 시작 가능)")
        return

    if args.stations:
        print(f"--stations 로 {len(pending_stations)}개 역 처리 (역당 식당/카페/술집 각 최대 {limit_per_query}개)")
    else:
        print(
            f"총 {len(stations)}개 역 중 잔여 {len(pending_stations)}개 처리 (역당 식당/카페/술집 각 최대 {limit_per_query}개)"
        )
    print("=" * 60)

    total_ingested, total_skipped = asyncio.run(
        crawl_all_stations(
            pending_stations,
            limit_per_query,
            headless=not args.headed,
            progress_path=progress_path,
            completed=completed,
        )
    )

    print("=" * 60)
    print(f"크롤링+DB upsert 완료. 신규 {total_ingested}개, 스킵 {total_skipped}개")
    if not args.skip_reviews:
        run_crawl_reviews(max_reviews=MAX_REVIEWS_PER_PLACE, limit_places=None)
    else:
        print("[리뷰 크롤링] --skip-reviews 로 생략", flush=True)


if __name__ == "__main__":
    main()
