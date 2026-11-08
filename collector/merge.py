"""Join the sources into app items (research.md §6-2 계약, 사실 정정 2026-11-09).

- Stamp point: the 15125127 coordinate (`stampSource: "list"`) unless
  `overrides/summits.json` has the mountain (`"override"`). POIs (15097953)
  are crowd survey points incl. neighbouring peaks, so they are never picked
  automatically; they only feed a consistency check (`poi_warnings`).
- 15125127 -> 15158915 by name (15125127 `mtnCd` has duplicates), then
  15158915 `mntncd` -> 15058662 `mntilistno`.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .config import OVERRIDE_WARN_M, POI_CHECK_M
from .geo import distance_m
from .overrides import Override, StampPoint
from .records import ListMountain, MountainInfo, Poi, TrailInfo

Document = Dict[str, Any]
SET_TOP100 = "top100"
LIST_POINT_NAME = "정상"
TEXT_FIELDS = ("subtitle", "reason", "summary", "details", "transport", "tourism", "courses")
_REGION_SUFFIX = re.compile(r"(특별시|광역시|특별자치시|특별자치도|시|군|구)$")


def mountain_id(mountain: ListMountain) -> str:
    return f"m-{mountain.frtrl_id}"


def _unique_by(rows: Iterable[TrailInfo], key) -> Dict[str, TrailInfo]:  # noqa: ANN001
    groups: Dict[str, List[TrailInfo]] = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    return {k: v[0] for k, v in groups.items() if len(v) == 1}


def join_trail(mountains: Sequence[ListMountain], trail: Sequence[TrailInfo]) -> Dict[str, TrailInfo]:
    """Mountain id -> 15158915 row: same name as given, else same base name
    when that base name is unique on both sides ("덕유산(향적봉)" -> 덕유산)."""
    exact = _unique_by(trail, lambda row: row.source_name)
    by_base = _unique_by(trail, lambda row: row.name)
    list_bases = Counter(m.name for m in mountains)
    joined: Dict[str, TrailInfo] = {}
    for mountain in mountains:
        row = exact.get(mountain.source_name)
        if row is None and list_bases[mountain.name] == 1:
            row = by_base.get(mountain.name)
        if row is not None:
            joined[mountain_id(mountain)] = row
    return joined


def join_info(
    mountains: Sequence[ListMountain],
    trail_by_id: Mapping[str, TrailInfo],
    info: Sequence[MountainInfo],
) -> Dict[str, MountainInfo]:
    """Mountain id -> 15058662 row by 산코드 (15158915 code, else the list
    code when no other list row shares it); the names must agree."""
    by_code = {row.code: row for row in info}
    list_codes = Counter(m.code for m in mountains if m.code)
    joined: Dict[str, MountainInfo] = {}
    for mountain in mountains:
        trail = trail_by_id.get(mountain_id(mountain))
        code = trail.code if trail and trail.code else (mountain.code if mountain.code and list_codes[mountain.code] == 1 else None)
        row = by_code.get(code) if code else None
        if row is not None and row.name.split("(")[0].strip() == mountain.name:
            joined[mountain_id(mountain)] = row
    return joined


def _region(address: str) -> Optional[str]:
    """"경상남도 산청군ㆍ하동군, …" -> "산청"."""
    parts = address.replace(",", " ").split()
    if len(parts) < 2:
        return None
    first = parts[1].split("ㆍ")[0]
    short = _REGION_SUFFIX.sub("", first)
    return short or None


def labels(mountains: Sequence[ListMountain]) -> Dict[str, str]:
    """Display names: the source's own bracket ("백운산 (포천)"); if two
    mountains would still look the same, the first 시군 of the address."""
    first = {mountain_id(m): f"{m.name} ({m.qualifier})" if m.qualifier else m.name for m in mountains}
    counts = Counter(first.values())
    result: Dict[str, str] = {}
    for m in mountains:
        label = first[mountain_id(m)]
        region = _region(m.address)
        if counts[label] > 1 and not m.qualifier and region:
            label = f"{m.name} ({region})"
        result[mountain_id(m)] = label
    return result


def stamp_points(mountain: ListMountain, override: Optional[Override]) -> Tuple[Tuple[StampPoint, ...], str]:
    if override is not None and override.stamp:
        return override.stamp, "override"
    return (StampPoint(mountain.lat, mountain.lng, LIST_POINT_NAME),), "list"


def override_warnings(mountains: Sequence[ListMountain], overrides: Mapping[str, Override]) -> List[str]:
    """Typos in the hand file: unknown ids, other names, far moves without
    `farFromSource`."""
    by_id = {mountain_id(m): m for m in mountains}
    warnings: List[str] = []
    for entry_id, override in sorted(overrides.items()):
        mountain = by_id.get(entry_id)
        if mountain is None:
            warnings.append(f"override {entry_id}: no such mountain in the list")
            continue
        if override.name is not None and override.name != mountain.source_name:
            warnings.append(f"override {entry_id}: name differs from the source ({mountain.source_name})")
        if override.stamp and not override.far_from_source:
            moved = distance_m(mountain.lat, mountain.lng, override.stamp[0].lat, override.stamp[0].lng)
            if moved > OVERRIDE_WARN_M:
                warnings.append(
                    f"override {entry_id} ({mountain.source_name}): {moved:.0f} m from the source "
                    "without farFromSource: true (typo?)"
                )
    return warnings


def poi_warnings(
    mountains: Sequence[ListMountain],
    overrides: Mapping[str, Override],
    pois: Sequence[Poi],
) -> Tuple[List[str], int]:
    """Warnings for mountains whose stamp points have no POI of the same
    mountain within POI_CHECK_M (checked `keepSource` entries excepted), and
    the number of mountains without any POI."""
    by_mountain: Dict[str, List[Poi]] = defaultdict(list)
    for poi in pois:
        by_mountain[poi.frtrl_id].append(poi)
    warnings: List[str] = []
    no_poi = 0
    for mountain in mountains:
        candidates = by_mountain.get(mountain.frtrl_id)
        if not candidates:
            no_poi += 1
            continue
        override = overrides.get(mountain_id(mountain))
        if override is not None and override.keep_source:
            continue
        points, _ = stamp_points(mountain, override)
        nearest = min(distance_m(p.lat, p.lng, poi.lat, poi.lng) for p in points for poi in candidates)
        if nearest > POI_CHECK_M:
            warnings.append(f"poi check {mountain_id(mountain)} ({mountain.source_name}): nearest POI {nearest:.0f} m")
    return warnings, no_poi


@dataclass(frozen=True)
class Reuse:
    """Which parts come from the previous file because a source failed."""

    text: bool = False
    updated: bool = False


def build_item(
    mountain: ListMountain,
    label: str,
    override: Optional[Override],
    trail: Optional[TrailInfo],
    info: Optional[MountainInfo],
    previous: Optional[Mapping[str, Any]],
    reuse: Reuse = Reuse(),
) -> Document:
    points, source = stamp_points(mountain, override)
    texts = {
        "subtitle": trail.subtitle if trail else "",
        "reason": (trail.reason if trail else "") or (info.reason if info else ""),
        "summary": (trail.summary if trail else "") or (info.summary if info else ""),
        "details": (trail.details if trail else "") or (info.details if info else ""),
        "transport": trail.transport if trail else "",
        "tourism": trail.tourism if trail else "",
        "courses": trail.courses if trail else "",
    }
    if reuse.text and previous:
        texts = {k: v or str(previous.get(k) or "") for k, v in texts.items()}
    updated = info.updated if info else None
    if updated is None and reuse.updated and previous and isinstance(previous.get("updated"), str):
        updated = previous["updated"]
    return {
        "id": mountain_id(mountain),
        "set": SET_TOP100,
        "name": mountain.name,
        "label": label,
        "sido": mountain.sido,
        "address": mountain.address,
        "heightM": mountain.height_m,
        "stamp": [point.to_json() for point in points],
        "stampSource": source,
        **texts,
        "updated": updated,
    }
