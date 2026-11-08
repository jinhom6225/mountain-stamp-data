"""Compare this run's items with the previous `data/mountains.json`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from .config import MOVE_REPORT_M
from .geo import distance_m

LIST_LIMIT = 10
Item = Mapping[str, Any]


@dataclass(frozen=True)
class Change:
    id: str
    label: str
    detail: str = ""

    def line(self, kind: str) -> str:
        return f"{kind}: {self.label} ({self.id})" + (f" {self.detail}" if self.detail else "")


@dataclass(frozen=True)
class Diff:
    new: Tuple[Change, ...]
    moved: Tuple[Change, ...]
    height: Tuple[Change, ...]
    updated: Tuple[Change, ...]
    removed: Tuple[Change, ...]

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.moved or self.height or self.updated or self.removed)

    def counts_text(self) -> str:
        return (
            f"새 {len(self.new)} · 지점 이동 {len(self.moved)} · 높이 바뀜 {len(self.height)} · "
            f"정보 바뀜 {len(self.updated)} · 사라짐 {len(self.removed)}"
        )

    def lines(self) -> List[str]:
        body: List[str] = []
        for kind, changes in (
            ("new", self.new),
            ("moved", self.moved),
            ("height", self.height),
            ("info", self.updated),
            ("removed", self.removed),
        ):
            body.extend(c.line(kind) for c in changes[:LIST_LIMIT])
            if len(changes) > LIST_LIMIT:
                body.append(f"{kind}: ... and {len(changes) - LIST_LIMIT} more")
        return body

    def commit_message(self, total: int, overridden: int) -> str:
        if self.has_changes:
            subject = (
                f"chore(data): update mountains (+{len(self.new)} new, ~{len(self.moved)} moved, "
                f"~{len(self.height)} height, ~{len(self.updated)} info, -{len(self.removed)} removed)"
            )
        else:
            subject = "chore(data): refresh mountain list (no mountain changes)"
        body = self.lines() + [f"mountains: {total}, stamp overrides: {overridden}"]
        return subject + "\n\n" + "\n".join(body) + "\n"


def _first_point(item: Item) -> Optional[Tuple[float, float]]:
    stamp = item.get("stamp")
    if not isinstance(stamp, list) or not stamp or not isinstance(stamp[0], dict):
        return None
    lat, lng = stamp[0].get("lat"), stamp[0].get("lng")
    if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
        return float(lat), float(lng)
    return None


def _moved(old: Item, new: Item) -> Optional[str]:
    a, b = _first_point(old), _first_point(new)
    if a is None or b is None:
        return "" if a != b else None
    meters = distance_m(a[0], a[1], b[0], b[1])
    if meters >= MOVE_REPORT_M:
        return f"{meters:.0f} m"
    old_stamp, new_stamp = old.get("stamp"), new.get("stamp")
    if isinstance(old_stamp, list) and isinstance(new_stamp, list) and len(old_stamp) != len(new_stamp):
        return f"points {len(old_stamp)} -> {len(new_stamp)}"
    return None


def compare(previous: Mapping[str, Item], current: Sequence[Item]) -> Diff:
    """new / stamp moved (first point >= 30 m or point count) / height /
    other fields / removed. One change kind per mountain, in that order."""
    new: List[Change] = []
    moved: List[Change] = []
    height: List[Change] = []
    updated: List[Change] = []
    seen = set()
    for item in current:
        item_id, label = item["id"], str(item.get("label") or item.get("name") or "")
        seen.add(item_id)
        old = previous.get(item_id)
        if old is None:
            new.append(Change(item_id, label))
            continue
        move = _moved(old, item)
        if move is not None:
            moved.append(Change(item_id, label, move))
        elif old.get("heightM") != item.get("heightM"):
            height.append(Change(item_id, label, f"{old.get('heightM')} -> {item.get('heightM')}"))
        elif dict(old) != dict(item):
            updated.append(Change(item_id, label))
    removed = [Change(i, str(old.get("label") or old.get("name") or "")) for i, old in previous.items() if i not in seen]
    return Diff(tuple(new), tuple(moved), tuple(height), tuple(updated), tuple(removed))
