"""
pgvector 인덱스 생성 스크립트
---------------------------
벡터 검색 성능 향상을 위한 인덱스 생성
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

from app.db.session import engine


def create_indexes() -> None:
    """벡터 검색 인덱스 생성."""
    print("🔧 pgvector 인덱스 생성 중...")
    
    with engine.connect() as conn:
        # review_embeddings 테이블의 embedding 컬럼에 인덱스 생성
        # ivfflat은 대용량 데이터에 적합한 인덱스 타입
        # lists 파라미터는 데이터 크기에 따라 조정 (일반적으로 sqrt(행 수))
        
        print("  - review_embeddings.embedding 인덱스 생성 중...")
        try:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS review_embeddings_embedding_idx 
                ON review_embeddings 
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100);
            """))
            conn.commit()
            print("  ✅ review_embeddings 인덱스 생성 완료")
        except Exception as e:
            print(f"  ⚠️  review_embeddings 인덱스 생성 실패: {e}")
            conn.rollback()
        
        # 추가 인덱스: 카테고리별 검색 성능 향상
        print("  - review_embeddings.category 인덱스 생성 중...")
        try:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS review_embeddings_category_idx 
                ON review_embeddings (category);
            """))
            conn.commit()
            print("  ✅ category 인덱스 생성 완료")
        except Exception as e:
            print(f"  ⚠️  category 인덱스 생성 실패: {e}")
            conn.rollback()
        
        # place_id 인덱스 (이미 있을 수 있음)
        print("  - review_embeddings.place_id 인덱스 확인 중...")
        try:
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS review_embeddings_place_id_idx 
                ON review_embeddings (place_id);
            """))
            conn.commit()
            print("  ✅ place_id 인덱스 생성 완료")
        except Exception as e:
            print(f"  ⚠️  place_id 인덱스 생성 실패: {e}")
            conn.rollback()
        
        print("\n✅ 인덱스 생성 완료")
        
        # 생성된 인덱스 확인
        print("\n📋 생성된 인덱스 목록:")
        result = conn.execute(text("""
            SELECT indexname, tablename 
            FROM pg_indexes 
            WHERE schemaname = 'public' 
            AND tablename = 'review_embeddings'
            ORDER BY indexname;
        """))
        for row in result:
            print(f"  - {row[1]}.{row[0]}")


if __name__ == "__main__":
    create_indexes()
