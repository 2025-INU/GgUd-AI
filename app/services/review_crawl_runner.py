"""Run review crawler and create embeddings."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.place import Place
from app.models.review import Review
from app.schemas.crawl import ReviewCrawlSummary
from app.services.recommendation import refresh_embeddings

# backend 폴더 내부의 scripts 폴더에서 크롤러 실행
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
REVIEW_SCRIPT = BACKEND_ROOT / "scripts" / "review_crawl.py"
PYTHON_BIN = sys.executable


def _upsert_review(db: Session, place_id: int, review_data: dict) -> Review | None:
    """리뷰를 DB에 저장/업데이트 후 반환 (임베딩 시 review.id 사용)."""
    rid = review_data.get("id") or review_data.get("review_id")
    if not rid:
        return None
    rid = str(rid)
    existing = db.query(Review).filter(Review.review_id == rid).first()
    content = (review_data.get("content") or "").strip()
    if existing:
        existing.content = content
        existing.author = review_data.get("author")
        existing.rating = review_data.get("rating")
        existing.crawled_at = datetime.now()
        db.commit()
        db.refresh(existing)
        return existing
    review = Review(
        place_id=place_id,
        review_id=rid,
        author=review_data.get("author"),
        content=content,
        rating=review_data.get("rating"),
        crawled_at=datetime.now(),
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def _run_command(args: list[str]) -> str:
    completed = subprocess.run(
        args,
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "Review crawler failed")
    return completed.stdout.strip()


def fetch_reviews_from_cli(place_id: int, max_count: int) -> list[dict[str, Any]]:
    cmd = [
        PYTHON_BIN,
        str(REVIEW_SCRIPT),
        "--place-id",
        str(place_id),
        "--max-count",
        str(max_count),
        "--json-output",
    ]
    stdout = _run_command(cmd)
    if not stdout:
        return []
    return json.loads(stdout)


def crawl_reviews_for_places(
    db: Session,
    place_ids: Iterable[int] | None,
    max_count: int,
) -> ReviewCrawlSummary:
    """Fetch reviews for given places and store embeddings."""
    if place_ids:
        ids = list(dict.fromkeys(place_ids))
    else:
        ids = [p.id for p in db.query(Place.id).all()]

    places_processed = 0
    embeddings_created = 0
    review_failures = 0

    for place_id in ids:
        try:
            reviews = fetch_reviews_from_cli(place_id, max_count)
        except Exception as exc:  # noqa: BLE001
            review_failures += 1
            print(f"[SKIP] place_id={place_id} review crawl failed: {exc}", file=sys.stderr)
            continue

        if not reviews:
            continue

        places_processed += 1
        print(f"[INFO] place_id={place_id}: {len(reviews)}개 리뷰 처리 시작", file=sys.stderr)

        reviews_processed = 0
        for review_data in reviews:
            content = (review_data.get("content") or "").strip()
            if not content:
                continue
            review_row = _upsert_review(db, place_id, review_data)
            if not review_row:
                continue
            reviews_processed += 1
            _, inserted = refresh_embeddings(db, place_id, review_row.id, content)
            embeddings_created += inserted
        
        print(f"[INFO] place_id={place_id}: {reviews_processed}개 리뷰 처리 완료, {embeddings_created}개 임베딩 생성", file=sys.stderr)

    return ReviewCrawlSummary(
        places_processed=places_processed,
        embeddings_created=embeddings_created,
        review_failures=review_failures,
    )


