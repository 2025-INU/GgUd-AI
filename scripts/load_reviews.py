"""
리뷰 데이터 적재 스크립트
----------------------
reviews.jsonl 파일을 읽어서 PostgreSQL에 저장합니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
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


def iter_jsonl(path: Path):
    """JSONL 파일을 한 줄씩 읽어 dict로 yield."""
    with path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


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


def upsert_review(db: Session, place_id: int, review_data: dict) -> Review | None:
    """리뷰 데이터를 DB에 저장 또는 업데이트."""
    review_id_str = review_data.get("id") or review_data.get("review_id")
    if not review_id_str:
        return None

    try:
        # 기존 리뷰 확인
        existing = db.query(Review).filter(Review.review_id == review_id_str).first()
        
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


def load_reviews(jsonl_path: Path, db: Session) -> tuple[int, int, int]:
    """리뷰 JSONL을 DB에 적재."""
    success = 0
    skipped = 0
    failed = 0

    for record in iter_jsonl(jsonl_path):
        place_id_str = record.get("place_id")
        if not place_id_str:
            skipped += 1
            continue

        try:
            place_id = int(place_id_str)
        except (ValueError, TypeError):
            skipped += 1
            continue

        # 장소가 DB에 존재하는지 확인
        place = db.get(Place, place_id)
        if not place:
            skipped += 1
            if skipped <= 5:
                print(f"[SKIP] place_id={place_id} 장소가 DB에 없음", file=sys.stderr)
            continue

        # 리뷰 내용 확인
        content = record.get("content", "").strip()
        if not content:
            skipped += 1
            if skipped <= 5:
                print(f"[SKIP] place_id={place_id} 리뷰 내용 없음", file=sys.stderr)
            continue
        
        # review_id 확인
        review_id_str = record.get("id") or record.get("review_id")
        if not review_id_str:
            skipped += 1
            if skipped <= 5:
                print(f"[SKIP] place_id={place_id} review_id 없음", file=sys.stderr)
            continue

        try:
            upsert_review(db, place_id, record)
            success += 1
            if success % 10 == 0:
                print(f"[INFO] {success}개 리뷰 적재 완료...", file=sys.stderr)
        except Exception as exc:
            # 트랜잭션 롤백 후 계속 진행
            db.rollback()
            failed += 1
            # 첫 번째 실패만 상세 로그 출력
            if failed == 1:
                import traceback
                print(f"[FAIL] place_id={place_id}, review_id={record.get('id')} 첫 번째 오류 상세:", file=sys.stderr)
                print(traceback.format_exc(), file=sys.stderr)
            elif failed <= 5:
                print(f"[FAIL] place_id={place_id}, review_id={record.get('id')} error={exc}", file=sys.stderr)

    return success, skipped, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="reviews.jsonl → PostgreSQL 적재")
    parser.add_argument(
        "--file",
        type=Path,
        default=Path("reviews.jsonl"),
        help="리뷰 JSONL 파일 경로 (기본: ./reviews.jsonl)",
    )
    args = parser.parse_args()

    if not args.file.exists():
        raise SystemExit(f"파일을 찾을 수 없습니다: {args.file}")

    db = SessionLocal()
    try:
        print(f"📖 {args.file}에서 리뷰 데이터 로드 중...")
        success, skipped, failed = load_reviews(args.file, db)
        
        print("\n" + "=" * 60)
        print("리뷰 적재 완료")
        print("=" * 60)
        print(f"  성공: {success}개")
        print(f"  건너뜀: {skipped}개")
        print(f"  실패: {failed}개")
        print("=" * 60)
    finally:
        db.close()


if __name__ == "__main__":
    main()
