"""places.origin_address 컬럼을 road_address로 리네이밍하는 스크립트.

이미 DB에 origin_address 컬럼이 있고, 코드에서는 road_address로 사용하도록 변경한 뒤
이 스크립트를 한 번 실행하세요.

사용법:
  python scripts/rename_place_origin_to_road_address.py
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


def rename_column() -> None:
    """places.origin_address → places.road_address 리네이밍."""
    with engine.connect() as conn:
        conn.execute(
            text("ALTER TABLE places RENAME COLUMN origin_address TO road_address")
        )
        conn.commit()
    print("places.origin_address 컬럼을 road_address로 리네이밍 완료")


if __name__ == "__main__":
    rename_column()

