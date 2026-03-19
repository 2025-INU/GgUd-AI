"""Schemas shared across review processing."""

from typing import Optional

from pydantic import BaseModel


class CategoryInfo(BaseModel):
    companion: Optional[str] = None
    menu: Optional[str] = None
    mood: Optional[str] = None
    purpose: Optional[str] = None
    # 사용자 쿼리 전용: 원하는 장소 업종(Place.category 필터용). 예: 카페, 한식, 이탈리아음식
    place_type: Optional[str] = None

