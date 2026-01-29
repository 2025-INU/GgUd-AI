"""Schemas for crawl orchestration."""

from typing import Optional

from pydantic import BaseModel, Field


class PlaceCrawlRequest(BaseModel):
    query: str = Field(..., description="네이버 지도 검색어")


class PlaceCrawlSummary(BaseModel):
    places_fetched: int
    places_skipped: int


class ReviewCrawlRequest(BaseModel):
    place_ids: Optional[list[int]] = Field(
        None, description="지정하지 않으면 모든 장소에 대해 리뷰를 크롤링"
    )
    max_count: int = Field(100, ge=1, le=200, description="장소별 리뷰 최대 수집 개수")


class ReviewCrawlSummary(BaseModel):
    places_processed: int
    embeddings_created: int
    review_failures: int


