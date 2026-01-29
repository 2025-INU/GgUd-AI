import json
from pathlib import Path
from typing import Dict, Iterable, List, Set


class PlaceStorageManager:
    """간단한 JSONL 기반 저장소"""

    def __init__(self, output_path: str = "places.jsonl") -> None:
        self.path = Path(output_path)
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_existing_place_ids(self) -> Set[str]:
        if not self.path.exists():
            return set()

        place_ids: Set[str] = set()
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                place_id = data.get("place_id")
                if place_id:
                    place_ids.add(str(place_id))
        return place_ids

    def append(self, places: Iterable[Dict]) -> None:
        if not places:
            return
        with self.path.open("a", encoding="utf-8") as f:
            for place in places:
                f.write(json.dumps(place, ensure_ascii=False) + "\n")


