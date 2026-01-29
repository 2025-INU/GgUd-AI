"""Place endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.place import PlaceCreate, PlaceOut
from app.services.recommendation import upsert_place
from app.models.place import Place

router = APIRouter(prefix="/places", tags=["places"])


@router.post("", response_model=PlaceOut)
def create_place(payload: PlaceCreate, db: Session = Depends(get_db)) -> PlaceOut:
    """Create or update a place."""
    place = upsert_place(db, payload.model_dump())
    return PlaceOut.model_validate(place)


@router.get("", response_model=list[PlaceOut])
def list_places(ids: str | None = None, db: Session = Depends(get_db)) -> list[PlaceOut]:
    """Return places; optionally filter by comma-separated ids."""
    query = db.query(Place)
    if ids:
        id_list = [int(i.strip()) for i in ids.split(",") if i.strip().isdigit()]
        if id_list:
            query = query.filter(Place.id.in_(id_list))
    places = query.all()
    return [PlaceOut.model_validate(p) for p in places]


