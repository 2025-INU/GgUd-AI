"""
장소 데이터 적재 스크립트
----------------------
places.jsonl 파일을 읽어서 PostgreSQL에 저장합니다.
"""

from __future__ import annotations

import argparse
import json
import os
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
from app.services.recommendation import upsert_place


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


def load_places(jsonl_path: Path, db: Session) -> tuple[int, int, int]:
    """장소 JSONL을 DB에 적재."""
    success = 0
    skipped = 0
    failed = 0

    for record in iter_jsonl(jsonl_path):
        place_id_str = record.get("place_id")
        if not place_id_str:
            skipped += 1
            if skipped <= 5:  # 처음 5개만 로그 출력
                print(f"[SKIP] place_id 없음: {record}", file=sys.stderr)
            continue

        try:
            place_id = int(place_id_str)
        except (ValueError, TypeError):
            skipped += 1
            if skipped <= 5:
                print(f"[SKIP] place_id 변환 실패: {place_id_str}", file=sys.stderr)
            continue

        # 필수 필드 확인
        name = record.get("name")
        category = record.get("category") or "기타"
        origin_address = record.get("origin_address") or record.get("address")
        latitude = record.get("latitude")
        longitude = record.get("longitude")

        if not all([name, origin_address, latitude is not None, longitude is not None]):
            skipped += 1
            missing_fields = []
            if not name:
                missing_fields.append("name")
            if not origin_address:
                missing_fields.append("origin_address")
            if latitude is None:
                missing_fields.append("latitude")
            if longitude is None:
                missing_fields.append("longitude")
            if skipped <= 5:
                print(f"[SKIP] place_id={place_id_str} 필수 필드 누락: {', '.join(missing_fields)}", file=sys.stderr)
            continue

        try:
            payload = {
                "id": place_id,
                "name": name,
                "category": category,
                "origin_address": origin_address,
                "latitude": float(latitude),
                "longitude": float(longitude),
                "crawled_at": datetime.now(),
            }
            upsert_place(db, payload)
            success += 1
            if success % 10 == 0:
                print(f"[INFO] {success}개 장소 적재 완료...", file=sys.stderr)
        except Exception as exc:
            # 트랜잭션 롤백 후 계속 진행
            db.rollback()
            failed += 1
            # 첫 번째 실패만 상세 로그 출력
            if failed == 1:
                import traceback
                print(f"[FAIL] place_id={place_id} 첫 번째 오류 상세:", file=sys.stderr)
                print(traceback.format_exc(), file=sys.stderr)
            elif failed <= 5:
                print(f"[FAIL] place_id={place_id} error={exc}", file=sys.stderr)

    return success, skipped, failed


def main() -> None:
    parser = argparse.ArgumentParser(description="places.jsonl → PostgreSQL 적재")
    parser.add_argument(
        "--file",
        type=Path,
        default=Path("places.jsonl"),
        help="장소 JSONL 파일 경로 (기본: ./places.jsonl)",
    )
    args = parser.parse_args()

    if not args.file.exists():
        raise SystemExit(f"파일을 찾을 수 없습니다: {args.file}")

    db = SessionLocal()
    try:
        print(f"📖 {args.file}에서 장소 데이터 로드 중...")
        success, skipped, failed = load_places(args.file, db)
        
        print("\n" + "=" * 60)
        print("장소 적재 완료")
        print("=" * 60)
        print(f"  성공: {success}개")
        print(f"  건너뜀: {skipped}개")
        print(f"  실패: {failed}개")
        print("=" * 60)
    finally:
        db.close()


if __name__ == "__main__":
    main()
