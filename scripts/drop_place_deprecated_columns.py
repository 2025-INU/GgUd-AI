"""
places 테이블에서 제거된 컬럼(road_address, rating) 삭제
-----------------------------------------------------
Place 모델에서 road_address, rating을 제거한 뒤 기존 DB에 컬럼이 남아 있으면
이 스크립트를 한 번 실행하세요.

사용법:
  python scripts/drop_place_deprecated_columns.py
"""

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_ROOT / ".env")
except Exception:
    pass

from sqlalchemy import text

from app.db.session import engine


def drop_deprecated_columns() -> None:
    """places.origin_address, places.rating 컬럼 삭제."""
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE places DROP COLUMN IF EXISTS origin_address"))
        conn.execute(text("ALTER TABLE places DROP COLUMN IF EXISTS rating"))
        conn.commit()
    print("places 테이블에서 road_address, rating 컬럼 제거 완료")


if __name__ == "__main__":
    drop_deprecated_columns()
