"""
데이터베이스 스키마 초기화 스크립트
----------------------------------
테이블을 생성하거나 업데이트합니다.
"""

import sys
from pathlib import Path

# 환경 변수 로드
from dotenv import load_dotenv

# backend 폴더의 .env 파일 로드
BACKEND_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_ROOT / ".env")

# backend 모듈 import를 위해 경로 추가
sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from app.models.place import Place
from app.models.place_summary_embedding import PlaceSummaryEmbedding
from app.db.session import engine


def init_db_schema() -> None:
    """데이터베이스 스키마 초기화."""
    print("🔧 데이터베이스 스키마 초기화 중...")
    
    # pgvector extension 생성
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
        print("✅ pgvector extension 생성 완료")
    
    # 필요한 테이블만 생성 (불필요 테이블 재생성 방지)
    Place.__table__.create(bind=engine, checkfirst=True)
    PlaceSummaryEmbedding.__table__.create(bind=engine, checkfirst=True)
    print("✅ 테이블 생성 완료")
    
    print("\n📋 생성된 테이블:")
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
            ORDER BY table_name
        """))
        for row in result:
            print(f"  - {row[0]}")


if __name__ == "__main__":
    init_db_schema()
