"""Pydantic schemas for places."""

from pydantic import BaseModel, Field


class PlaceBase(BaseModel):
    id: int = Field(..., description="Unique place identifier")
    name: str
    category: str
    origin_address: str
    latitude: float
    longitude: float

    model_config = {"from_attributes": True}


class PlaceCreate(BaseModel):
    id: int
    name: str
    category: str
    origin_address: str
    latitude: float
    longitude: float


class PlaceOut(PlaceBase):
    pass


