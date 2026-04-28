"""Database initialization utilities."""

from sqlalchemy import text

from app.db.session import engine
from app.models.place import Place
from app.models.place_summary_embedding import PlaceSummaryEmbedding


def init_db() -> None:
    """Create pgvector extension (if needed) and tables."""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    # 전체 metadata create_all()을 쓰면 Review/Embedding 모델 import 시
    # 불필요한 테이블이 재생성될 수 있어 필요한 테이블만 명시적으로 생성한다.
    Place.__table__.create(bind=engine, checkfirst=True)
    PlaceSummaryEmbedding.__table__.create(bind=engine, checkfirst=True)


