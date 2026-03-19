"""
review_embeddings 테이블에 review_id 추가 및 유니크 제약 변경.
리뷰별로 동일 문장도 별도 행 저장.

실행: python scripts/migrate_review_embeddings_add_review_id.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from sqlalchemy import text
from app.db.session import SessionLocal


def main():
    db = SessionLocal()
    try:
        db.execute(text("ALTER TABLE review_embeddings DROP CONSTRAINT IF EXISTS uq_review_category_value;"))
        db.commit()
    except Exception as e:
        db.rollback()
        print("Drop old constraint:", e, file=sys.stderr)

    try:
        r = db.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'review_embeddings' AND column_name = 'review_id';
        """))
        if r.scalar() is None:
            db.execute(text("TRUNCATE TABLE review_embeddings;"))
            db.commit()
            db.execute(text("""
                ALTER TABLE review_embeddings
                ADD COLUMN review_id INTEGER NOT NULL
                REFERENCES reviews(id) ON DELETE CASCADE;
            """))
            db.commit()
    except Exception as e:
        db.rollback()
        print("Add column:", e, file=sys.stderr)
        raise

    try:
        db.execute(text("""
            ALTER TABLE review_embeddings
            ADD CONSTRAINT uq_review_embedding_per_review
            UNIQUE (place_id, review_id, category, value_text);
        """))
        db.commit()
        print("Migration done.")
    except Exception as e:
        db.rollback()
        if "already exists" not in str(e).lower():
            raise
        print("New constraint already exists.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
