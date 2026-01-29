"""Schemas for recommendation flow."""

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.place import PlaceOut
from app.schemas.review import CategoryInfo


class RecommendationRequest(BaseModel):
    query: str = Field(..., description="사용자 자연어 요청")
    limit: Optional[int] = Field(None, ge=1, le=20, description="추천 개수")


class RecommendationDebug(BaseModel):
    extracted_categories: CategoryInfo


class RecommendationResponse(BaseModel):
    items: list[PlaceOut]
    meta: RecommendationDebug


# Spring Boot 호출 형식에 맞춘 스키마
class PlaceRecommendRequest(BaseModel):
    """Spring Boot 호출 형식 요청"""
    query: str = Field(..., description="사용자 자연어 요청")
    promise_id: Optional[int] = Field(None, description="약속 ID")
    limit: Optional[int] = Field(5, ge=1, le=20, description="추천 개수 (기본 5개)")


class PlaceRecommendationItem(BaseModel):
    """Spring Boot 호출 형식 응답 항목"""
    place_id: str = Field(..., description="장소 ID")
    place_name: str = Field(..., description="장소 이름")
    category: str = Field(..., description="카테고리")
    address: str = Field(..., description="주소")
    latitude: float = Field(..., description="위도")
    longitude: float = Field(..., description="경도")
    ai_score: Optional[float] = Field(None, description="AI 점수 (0~100)")
    distance_from_midpoint: Optional[float] = Field(None, description="중간지점으로부터의 거리 (km)")


class PlaceRecommendResponse(BaseModel):
    """Spring Boot 호출 형식 응답"""
    promise_id: Optional[int] = Field(None, description="약속 ID")
    recommendations: list[PlaceRecommendationItem] = Field(default_factory=list, description="추천 장소 목록")
