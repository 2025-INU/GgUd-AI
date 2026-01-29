"""Review model."""

from sqlalchemy import BigInteger, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class Review(Base):
    """Review data from Naver Place."""

    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    place_id = Column(BigInteger, ForeignKey("places.id", ondelete="CASCADE"), nullable=False, index=True)
    review_id = Column(String(100), nullable=False, unique=True, index=True)  # 네이버 리뷰 ID
    author = Column(String(100))  # 작성자 닉네임
    content = Column(Text, nullable=False)  # 리뷰 내용
    rating = Column(Float)  # 평점 (0-5)
    visit_date = Column(DateTime)  # 방문 날짜
    created_at = Column(DateTime)  # 리뷰 작성일
    crawled_at = Column(DateTime, nullable=False)  # 크롤링 시점

    place = relationship("Place", back_populates="reviews")
