"""Place model."""

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, String, Text

from app.db.base import Base


class Place(Base):
    """Place metadata."""

    __tablename__ = "places"

    id = Column(BigInteger, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    category = Column(String(100), nullable=False)
    road_address = Column(Text, nullable=False)
    image_url = Column(Text)
    ai_summary = Column(Text)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    review_count = Column(Integer, default=0)
    crawled_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime)


