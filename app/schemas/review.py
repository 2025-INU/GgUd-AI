"""Schemas shared across review processing."""

from typing import Optional

from pydantic import BaseModel


class CategoryInfo(BaseModel):
    companion: Optional[str] = None
    menu: Optional[str] = None
    mood: Optional[str] = None
    purpose: Optional[str] = None


