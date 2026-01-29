"""Review embedding model for individual review embeddings."""

from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from pgvector.sqlalchemy import Vector

from app.db.base import Base


class PlaceEmbedding(Base):
    """리뷰에서 추출한 카테고리별 임베딩을 저장하는 테이블.
    
    리뷰 텍스트에서 추출한 companion, menu, mood, purpose 카테고리 값을
    임베딩하여 저장합니다.
    """

    __tablename__ = "review_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "place_id",
            "category",
            "value_text",
            name="uq_review_category_value",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    place_id = Column(ForeignKey("places.id", ondelete="CASCADE"), nullable=False)
    category = Column(String(50), nullable=False)  # companion, menu, mood, purpose
    value_text = Column(Text, nullable=False)  # 카테고리 값 (예: "친구", "카페", "조용한")
    embedding = Column(Vector(1536), nullable=False)  # 카테고리 값의 임베딩

    place = relationship("Place")
