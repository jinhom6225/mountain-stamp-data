"""API rows -> validated records.

Original numbers (height, coordinates) are kept as given; only the type
changes. Text fields are cleaned (textclean). Messages of RecordInvalid name
the field, never the value.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from .config import KOREA_BOX, MAX_HEIGHT_M, SIDO_SHORT
from .textclean import clean_text

_SAFE_ID = re.compile(r"^[A-Za-z0-9_]{1,32}$")
_CODE = re.compile(r"^[0-9]{9}$")
_QUALIFIED = re.compile(r"^(?P<base>.+?)\s*\((?P<qualifier>[^()]+)\)$")
_DATE8 = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


class RecordInvalid(ValueError):
    """One row cannot be used."""


@dataclass(frozen=True)
class ListMountain:
    """15125127 row. `source_name` is the name as given ("백운산(포천)"),
    `name` the base name and `qualifier` the part in brackets."""

    frtrl_id: str
    source_name: str
    name: str
    qualifier: Optional[str]
    code: Optional[str]
    sido: str
    address: str
    lat: float
    lng: float
    height_m: float
    extracted: str


@dataclass(frozen=True)
class Poi:
    poi_id: str
    frtrl_id: str
    name: str
    lat: float
    lng: float


@dataclass(frozen=True)
class TrailInfo:
    """15158915 (명산등산로) row."""

    code: Optional[str]
    source_name: str
    name: str
    subtitle: str
    reason: str
    summary: str
    details: str
    transport: str
    tourism: str
    courses: str


@dataclass(frozen=True)
class MountainInfo:
    """15058662 (산정보) row."""

    code: str
    name: str
    reason: str
    summary: str
    details: str
    updated: Optional[str]


def split_name(name: str) -> Tuple[str, Optional[str]]:
    """"백운산(포천)" -> ("백운산", "포천"); "가리산" -> ("가리산", None)."""
    match = _QUALIFIED.match(name)
    if not match:
        return name, None
    return match.group("base").strip(), match.group("qualifier").strip()


_PLACEHOLDERS = frozenset({"-", "( - )", "(-)"})


def clean_rich(value: Any) -> str:
    """Text from 산림청 fields, which are sometimes HTML-escaped twice
    (`&amp;lt;br&amp;gt;`): clean, then clean the result again. The
    placeholder "( - )" (15058662 rows without a text) becomes ""."""
    text = clean_text(clean_text(value))
    return "" if text in _PLACEHOLDERS else text


def _row(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, dict):
        raise RecordInvalid("row is not an object")
    return raw


def _safe_id(raw: Mapping[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not _SAFE_ID.match(value):
        raise RecordInvalid(f"bad {field}")
    return value


def _required_text(raw: Mapping[str, Any], field: str) -> str:
    text = clean_text(raw.get(field))
    if not text:
        raise RecordInvalid(f"empty {field}")
    return text


def _number(raw: Mapping[str, Any], field: str) -> float:
    value = raw.get(field)
    if isinstance(value, bool):
        raise RecordInvalid(f"bad {field}")
    try:
        number = float(str(value).strip())
    except ValueError:
        raise RecordInvalid(f"bad {field}") from None
    if not math.isfinite(number):
        raise RecordInvalid(f"bad {field}")
    return number


def _point(raw: Mapping[str, Any], lat_field: str = "lat", lng_field: str = "lot") -> Tuple[float, float]:
    lat, lng = _number(raw, lat_field), _number(raw, lng_field)
    south, west, north, east = KOREA_BOX
    if not (south <= lat <= north and west <= lng <= east):
        raise RecordInvalid("coordinate outside Korea")
    return lat, lng


def _code(value: Any) -> Optional[str]:
    text = clean_text(value)
    return text if _CODE.match(text) else None


def parse_list_mountain(raw: Any) -> ListMountain:
    row = _row(raw)
    frtrl_id = _safe_id(row, "frtrlId")
    source_name = _required_text(row, "frtrlNm")
    name, qualifier = split_name(source_name)
    lat, lng = _point(row)
    height = _number(row, "aslAltide")
    if not 0 < height <= MAX_HEIGHT_M:
        raise RecordInvalid("bad aslAltide")
    sido_raw = _required_text(row, "ctpvNm")
    return ListMountain(
        frtrl_id=frtrl_id,
        source_name=source_name,
        name=name,
        qualifier=qualifier,
        code=_code(row.get("mtnCd")),
        sido=SIDO_SHORT.get(sido_raw, sido_raw),
        address=clean_text(row.get("addrNm")),
        lat=lat,
        lng=lng,
        height_m=height,
        extracted=clean_text(row.get("crtrDt")),
    )


def parse_poi(raw: Any) -> Poi:
    row = _row(raw)
    lat, lng = _point(row)
    return Poi(
        poi_id=_safe_id(row, "poiId"),
        frtrl_id=_safe_id(row, "frtrlId"),
        name=clean_text(row.get("placeNm")),
        lat=lat,
        lng=lng,
    )


def parse_trail_info(raw: Any) -> TrailInfo:
    row = _row(raw)
    source_name = _required_text(row, "mntnm")
    return TrailInfo(
        code=_code(row.get("mntncd")),
        source_name=source_name,
        name=split_name(source_name)[0],
        subtitle=clean_rich(row.get("subnm")),
        reason=clean_rich(row.get("aeatreason")),
        summary=clean_rich(row.get("overview")),
        details=clean_rich(row.get("details")),
        transport=clean_rich(row.get("transport")),
        tourism=clean_rich(row.get("tourisminf")),
        courses=clean_rich(row.get("etccourse")),
    )


def parse_mountain_info(raw: Any) -> MountainInfo:
    row = _row(raw)
    code = _code(row.get("mntilistno"))
    if code is None:
        raise RecordInvalid("bad mntilistno")
    date = _DATE8.match(clean_text(row.get("mntinfdt")))
    return MountainInfo(
        code=code,
        name=_required_text(row, "mntiname"),
        reason=clean_rich(row.get("mntitop")),
        summary=clean_rich(row.get("mntisummary")),
        details=clean_rich(row.get("mntidetails")),
        updated="-".join(date.groups()) if date else None,
    )
