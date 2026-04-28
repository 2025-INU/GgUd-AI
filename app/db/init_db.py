"""Database initialization utilities."""

from sqlalchemy import text

from app.db.base import Base
from app.db.session import engine
from app.models.place import Place  # noqa: F401
from app.models.place_summary_embedding import PlaceSummaryEmbedding  # noqa: F401


def init_db() -> None:
    """Create pgvector extension (if needed) and tables."""
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
    Base.metadata.create_all(bind=engine)


