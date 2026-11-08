"""`overrides/summits.json`: hand-checked stamp points (research.md §6-2).

The file wins over the source. Each entry is either
- `stamp`: one to five points (the first is the main point, the rest are
  alternates), with `farFromSource: true` when the main point is more than
  2 km from the source coordinate (otherwise a typo warning is printed), or
- `keepSource: true`: the source coordinate was checked and is kept (the
  weekly POI check then stays quiet for that mountain).
A broken file stops the run (ConfigError): dropping overrides silently would
move stamp points back to known-wrong places.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .config import COORD_DECIMALS, KOREA_BOX
from .errors import ConfigError

_ID = re.compile(r"^[mp]-[A-Za-z0-9_]{1,32}$")
MAX_POINTS = 5
MAX_NAME = 40


@dataclass(frozen=True)
class StampPoint:
    lat: float
    lng: float
    name: str

    def to_json(self) -> Dict[str, Any]:
        return {"lat": round(self.lat, COORD_DECIMALS), "lng": round(self.lng, COORD_DECIMALS), "name": self.name}


@dataclass(frozen=True)
class Override:
    id: str
    name: Optional[str]
    stamp: Tuple[StampPoint, ...]
    far_from_source: bool
    keep_source: bool


def _fail(entry_id: str, reason: str) -> ConfigError:
    return ConfigError(f"overrides/summits.json {entry_id}: {reason}")


def _coordinate(value: Any, low: float, high: float) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and low <= number <= high else None


def _point(entry_id: str, raw: Any) -> StampPoint:
    if not isinstance(raw, dict):
        raise _fail(entry_id, "stamp point is not an object")
    south, west, north, east = KOREA_BOX
    lat, lng = _coordinate(raw.get("lat"), south, north), _coordinate(raw.get("lng"), west, east)
    name = raw.get("name")
    if lat is None or lng is None:
        raise _fail(entry_id, "stamp point outside Korea or not a number")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME:
        raise _fail(entry_id, "stamp point needs a short name")
    return StampPoint(lat, lng, name.strip())


def _flag(entry_id: str, raw: Mapping[str, Any], field: str) -> bool:
    value = raw.get(field, False)
    if not isinstance(value, bool):
        raise _fail(entry_id, f"{field} must be true or false")
    return value


def parse_entry(entry_id: str, raw: Any) -> Override:
    if not _ID.match(entry_id):
        raise _fail("?", "bad mountain id")
    if not isinstance(raw, dict):
        raise _fail(entry_id, "entry is not an object")
    keep = _flag(entry_id, raw, "keepSource")
    far = _flag(entry_id, raw, "farFromSource")
    points = raw.get("stamp")
    name = raw.get("name")
    if name is not None and not isinstance(name, str):
        raise _fail(entry_id, "name must be text")
    if keep:
        if points is not None:
            raise _fail(entry_id, "keepSource entries have no stamp")
        return Override(entry_id, name, (), far, True)
    if not isinstance(points, list) or not 1 <= len(points) <= MAX_POINTS:
        raise _fail(entry_id, f"stamp needs 1 to {MAX_POINTS} points (or keepSource: true)")
    return Override(entry_id, name, tuple(_point(entry_id, p) for p in points), far, False)


def parse_overrides(text: str) -> Dict[str, Override]:
    try:
        document = json.loads(text)
    except ValueError:
        raise ConfigError("overrides/summits.json is not valid JSON") from None
    items = document.get("items") if isinstance(document, dict) else None
    if not isinstance(items, dict):
        raise ConfigError("overrides/summits.json has no items object")
    return {entry_id: parse_entry(entry_id, raw) for entry_id, raw in items.items()}


def load_overrides(path: Path) -> Dict[str, Override]:
    """Overrides by mountain id; a missing file means none."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        raise ConfigError("overrides/summits.json unreadable") from None
    return parse_overrides(text)
