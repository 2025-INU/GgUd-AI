"""
지하철역 CSV 기준으로 역별 "역명 맛집", "역명 카페" 자동 크롤링.

플로우: 장소 크롤링 → DB 적재 → 리뷰 크롤링 → 임베딩 생성 (한 번에 실행)

사용법:
  # 기본: 장소 + DB 적재 + 리뷰 + 임베딩
  python scripts/crawl_near_stations.py

  # 테스트: 상위 3개 역만, 리뷰 10개씩
  python scripts/crawl_near_stations.py --max-stations 3 --max-reviews 10

  # DB 적재만 (리뷰/임베딩 스킵)
  python scripts/crawl_near_stations.py --no-reviews

  # DB 적재 안 함
  python scripts/crawl_near_stations.py --no-load
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# GgUd-AI 프로젝트 루트
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = SCRIPT_DIR.parent
# Backend CSV (ggud_local/Backend/...)
BACKEND_CSV = BACKEND_ROOT.parent / "Backend" / "src" / "main" / "resources" / "data" / "seoul_subway_stations.csv"


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


def run_naver_crawl(query: str, limit: int = 30, verbose: bool = True) -> bool:
    """naver_crawl.py를 서브프로세스로 실행. 성공 여부 반환."""
    cmd = [
        sys.executable,
        str(BACKEND_ROOT / "scripts" / "naver_crawl.py"),
        "--query", query,
        "--limit", str(limit),
    ]
    if verbose:
        print(f"  [크롤링] {query}", flush=True)
    result = subprocess.run(cmd, cwd=str(BACKEND_ROOT))
    return result.returncode == 0


def run_load_places(jsonl_path: Path) -> bool:
    """load_places.py 실행. places.jsonl → DB 적재."""
    cmd = [
        sys.executable,
        str(BACKEND_ROOT / "scripts" / "load_places.py"),
        "--file", str(jsonl_path),
    ]
    print("\n[DB 적재] places.jsonl → PostgreSQL", flush=True)
    result = subprocess.run(cmd, cwd=str(BACKEND_ROOT))
    return result.returncode == 0


def run_crawl_reviews(max_reviews: int = 20, limit_places: int | None = None) -> bool:
    """crawl_reviews_from_db.py 실행. DB 장소별 리뷰 크롤링 → reviews 테이블 저장."""
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


def run_generate_embeddings(limit_places: int | None = None) -> bool:
    """generate_embeddings.py 실행. 리뷰 → place_embeddings 생성 (추천에 사용)."""
    cmd = [
        sys.executable,
        str(BACKEND_ROOT / "scripts" / "generate_embeddings.py"),
    ]
    if limit_places is not None:
        cmd.extend(["--limit", str(limit_places)])
    print("\n[임베딩 생성] 리뷰에서 카테고리 추출 및 임베딩 생성", flush=True)
    result = subprocess.run(cmd, cwd=str(BACKEND_ROOT))
    return result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="지하철역 CSV 기준 역별 맛집/카페 자동 크롤링",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=BACKEND_CSV,
        help=f"지하철역 CSV 경로 (기본: {BACKEND_CSV})",
    )
    parser.add_argument(
        "--limit-per-query",
        type=int,
        default=30,
        help="역당 맛집/카페 검색 시 각각 최대 개수 (기본: 30)",
    )
    parser.add_argument(
        "--max-stations",
        type=int,
        default=None,
        help="처리할 역 개수 제한 (테스트용, 기본: 전부)",
    )
    parser.add_argument(
        "--no-load",
        action="store_true",
        help="크롤링만 하고 load_places(DB 적재) 하지 않음",
    )
    parser.add_argument(
        "--no-reviews",
        action="store_true",
        help="DB 적재만 하고 리뷰 크롤링·임베딩 생성 스킵",
    )
    parser.add_argument(
        "--max-reviews",
        type=int,
        default=20,
        help="장소당 리뷰 최대 수집 개수 (기본: 20)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="크롤링 쿼리 로그 최소화",
    )
    args = parser.parse_args()

    stations = load_station_names(args.csv)
    if args.max_stations is not None:
        stations = stations[: args.max_stations]
    if not stations:
        print("역 목록이 비어 있습니다.", file=sys.stderr)
        sys.exit(1)

    print(f"총 {len(stations)}개 역 대상 (역당 맛집/카페 각 {args.limit_per_query}개)")
    print("=" * 60)

    for i, name in enumerate(stations, 1):
        print(f"[{i}/{len(stations)}] {name}")
        run_naver_crawl(f"{name} 맛집", limit=args.limit_per_query, verbose=not args.quiet)
        run_naver_crawl(f"{name} 카페", limit=args.limit_per_query, verbose=not args.quiet)

    print("=" * 60)
    print("크롤링 완료.")

    if not args.no_load:
        jsonl_path = BACKEND_ROOT / "places.jsonl"
        if jsonl_path.exists():
            run_load_places(jsonl_path)

            if not args.no_reviews:
                # max_stations 있을 때만 리뷰/임베딩 처리 장소 수 제한 (테스트용)
                limit_places = args.max_stations * 10 if args.max_stations else None
                run_crawl_reviews(max_reviews=args.max_reviews, limit_places=limit_places)
                run_generate_embeddings(limit_places=limit_places)
        else:
            print("places.jsonl이 없어 DB 적재를 건너뜁니다.", file=sys.stderr)


if __name__ == "__main__":
    main()
