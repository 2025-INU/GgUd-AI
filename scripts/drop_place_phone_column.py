"""places 테이블에서 phone 컬럼 삭제 스크립트.

사용법:
  python scripts/drop_place_phone_column.py
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


def drop_phone_column() -> None:
    """places.phone 컬럼 삭제."""
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE places DROP COLUMN IF EXISTS phone"))
        conn.commit()
    print("places 테이블에서 phone 컬럼 제거 완료")


if __name__ == "__main__":
    drop_phone_column()

