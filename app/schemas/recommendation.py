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


class RecommendationItem(PlaceOut):
    """추천 결과 한 건 (장소 정보 + 유사도 점수)."""
    ai_score: Optional[float] = Field(None, description="AI 점수 (0~100)")
    similarity_score: Optional[float] = Field(None, description="유사도 검색 raw 점수 (가중 합)")


class RecommendationResponse(BaseModel):
    items: list[RecommendationItem]
    meta: RecommendationDebug


# Spring Boot 호출 형식에 맞춘 스키마
class PlaceRecommendRequest(BaseModel):
    """Spring Boot 호출 형식 요청"""
    query: str = Field(..., description="사용자 자연어 요청")
    promise_id: Optional[int] = Field(None, description="약속 ID")
    limit: Optional[int] = Field(10, ge=1, le=20, description="추천 개수 (기본 10개)")
    latitude: Optional[float] = Field(None, description="중간지점 위도")
    longitude: Optional[float] = Field(None, description="중간지점 경도")


class PlaceRecommendationItem(BaseModel):
    """Spring Boot 호출 형식 응답 항목"""
    place_id: str = Field(..., description="장소 ID (PK)")
    ai_score: Optional[float] = Field(None, description="AI 점수 (0~100)")
    similarity_score: Optional[float] = Field(None, description="유사도 검색 raw 점수 (가중 합, 정규화 전)")
    distance_from_midpoint: Optional[float] = Field(None, description="중간지점으로부터의 거리 (km)")
    place_name: Optional[str] = Field(None, description="장소 이름 (Backend S3)")
    category: Optional[str] = Field(None, description="카테고리")
    address: Optional[str] = Field(None, description="주소")
    latitude: Optional[float] = Field(None, description="위도")
    longitude: Optional[float] = Field(None, description="경도")


class PlaceRecommendResponse(BaseModel):
    """Spring Boot 호출 형식 응답"""
    promise_id: Optional[int] = Field(None, description="약속 ID")
    recommendations: list[PlaceRecommendationItem] = Field(default_factory=list, description="추천 장소 목록")
