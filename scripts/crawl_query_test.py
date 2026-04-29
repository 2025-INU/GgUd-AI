"""
DB/S3 저장 없이 쿼리 기반 크롤링 결과를 터미널에 출력하는 테스트 스크립트.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from naver_crawl import crawl_places


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="쿼리 1개를 받아 네이버 장소를 크롤링하고 터미널에 출력합니다."
    )
    parser.add_argument(
        "--query",
        required=True,
        help="검색어 (예: 강남 분위기 좋은 카페)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="터미널에 출력할 최대 결과 개수 (기본값: 10)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="결과를 JSON 배열 형태로 출력",
    )
    return parser


def _print_pretty(results: list[dict]) -> None:
    if not results:
        print("크롤링 결과가 없습니다.")
        return

    print(f"총 {len(results)}개 결과")
    for idx, item in enumerate(results, 1):
        place_id = item.get("place_id")
        name = item.get("name") or "이름 없음"
        category = item.get("category") or "-"
        address = item.get("road_address") or item.get("address") or "-"
        review_count = item.get("review_count")
        score_text = f", reviews={review_count}" if review_count is not None else ""
        print(f"{idx}. [{place_id}] {name} ({category}) - {address}{score_text}")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    query = args.query.strip()
    if not query:
        raise SystemExit("--query는 공백일 수 없습니다.")
    if args.limit <= 0:
        raise SystemExit("--limit는 1 이상의 정수여야 합니다.")

    print(f"크롤링 시작: query='{query}', limit={args.limit}", file=sys.stderr)
    results = asyncio.run(crawl_places(query=query, limit=args.limit, verbose=True))

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_pretty(results)


if __name__ == "__main__":
    main()
