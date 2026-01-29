"""Run crawler scripts and ingest results."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.schemas.crawl import PlaceCrawlSummary
from app.services.recommendation import upsert_place
from app.models.place import Place

# backend 폴더 내부의 scripts 폴더에서 크롤러 실행
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
NAVER_SCRIPT = BACKEND_ROOT / "scripts" / "naver_crawl.py"
PYTHON_BIN = sys.executable


def _run_command(args: list[str]) -> str:
    """Run a subprocess command and return stdout."""
    completed = subprocess.run(
        args,
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "Crawler failed")
    return completed.stdout.strip()


def fetch_places_from_cli(query: str) -> list[dict[str, Any]]:
    """Invoke naver_crawl.py and return place dicts."""
    cmd = [PYTHON_BIN, str(NAVER_SCRIPT), "--query", query, "--json-output"]
    stdout = _run_command(cmd)
    if not stdout:
        return []
    return json.loads(stdout)


def ingest_from_crawl(
    db: Session,
    query: str,
) -> PlaceCrawlSummary:
    """Run crawlers and insert place metadata into DB."""
    places = fetch_places_from_cli(query)
    places_ingested = 0
    places_skipped = 0

    for place in places:
        # place_id가 없거나 None이면 스킵
        place_id_raw = place.get("place_id")
        if not place_id_raw:
            continue
        try:
            place_id = int(place_id_raw)
        except (ValueError, TypeError):
            continue

        origin_address = place.get("origin_address") or place.get("address")
        if not origin_address:
            continue
        latitude = place.get("latitude")
        longitude = place.get("longitude")
        if latitude is None or longitude is None:
            continue

        # DB에 이미 존재하는 경우에만 스킵
        if db.get(Place, place_id):
            places_skipped += 1
            continue

        payload = {
            "id": int(place["place_id"]),
            "name": place["name"],
            "category": place.get("category") or "기타",
            "origin_address": origin_address,
            "latitude": float(latitude),
            "longitude": float(longitude),
        }
        upsert_place(db, payload)
        places_ingested += 1

    return PlaceCrawlSummary(
        places_fetched=places_ingested,
        places_skipped=places_skipped,
    )


