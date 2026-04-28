"""Place-level summary embedding model."""

from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from app.db.base import Base


class PlaceSummaryEmbedding(Base):
    """장소별 리뷰 요약에서 추출한 카테고리 임베딩을 저장."""

    __tablename__ = "place_summary_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "place_id",
            "category",
            "value_text",
            name="uq_place_summary_embedding",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    place_id = Column(ForeignKey("places.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(50), nullable=False)
    value_text = Column(Text, nullable=False)
    summary_text = Column(Text, nullable=False)
    embedding = Column(Vector(1536), nullable=False)

    place = relationship("Place")
