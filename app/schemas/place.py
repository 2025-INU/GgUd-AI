"""Pydantic schemas for places."""

from pydantic import BaseModel, Field


class PlaceBase(BaseModel):
    id: int = Field(..., description="Unique place identifier")
    name: str
    category: str
    road_address: str
    image_url: str | None = Field(None, description="대표 이미지 URL")
    ai_summary: str | None = Field(None, description="네이버 지도 AI 요약 문구")
    latitude: float
    longitude: float

    model_config = {"from_attributes": True}


class PlaceCreate(BaseModel):
    id: int
    name: str
    category: str
    road_address: str
    image_url: str | None = None
    ai_summary: str | None = None
    latitude: float
    longitude: float


class PlaceOut(PlaceBase):
    pass


