"""Endpoints for orchestrated crawling."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.crawl import (
    PlaceCrawlRequest,
    PlaceCrawlSummary,
    ReviewCrawlRequest,
    ReviewCrawlSummary,
)
from app.services.crawl_runner import ingest_from_crawl
from app.services.review_crawl_runner import crawl_reviews_for_places

router = APIRouter(prefix="/crawl", tags=["crawl"])


@router.post("", response_model=PlaceCrawlSummary)
def crawl_places(payload: PlaceCrawlRequest, db: Session = Depends(get_db)) -> PlaceCrawlSummary:
    """Trigger place crawling via subprocess."""
    try:
        return ingest_from_crawl(
            db,
            query=payload.query,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/reviews", response_model=ReviewCrawlSummary)
def crawl_reviews(payload: ReviewCrawlRequest, db: Session = Depends(get_db)) -> ReviewCrawlSummary:
    """Trigger review crawling for stored places."""
    try:
        place_ids = payload.place_ids or None
        return crawl_reviews_for_places(
            db,
            place_ids=place_ids,
            max_count=payload.max_count,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


