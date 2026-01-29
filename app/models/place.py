"""Place model."""

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class Place(Base):
    """Place metadata."""

    __tablename__ = "places"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    category = Column(String(100), nullable=False)
    origin_address = Column(Text, nullable=False)
    road_address = Column(Text)  # 도로명 주소
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    rating = Column(Float)  # 평균 평점
    review_count = Column(Integer, default=0)  # 리뷰 개수
    phone = Column(String(50))  # 전화번호
    crawled_at = Column(DateTime, nullable=False)  # 크롤링 시점
    updated_at = Column(DateTime)  # 마지막 업데이트 시점

    reviews = relationship("Review", back_populates="place", cascade="all, delete-orphan")


