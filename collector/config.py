"""Fixed settings for one collector run. No secrets live here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Final, Mapping, Tuple

SCHEMA_VERSION: Final = 1
KST: Final = timezone(timedelta(hours=9))


@dataclass(frozen=True)
class Source:
    """One data.go.kr operation. `fmt` is "json" (한국등산트레킹지원센터,
    `resultCode` "0") or "xml" (산림청, `resultCode` "00")."""

    id: str
    name: str
    url: str
    key_param: str
    fmt: str
    page_size: int
    page: str
    extra: Tuple[Tuple[str, str], ...] = ()


KTC_BASE: Final = "https://apis.data.go.kr/B553662"
KFS_BASE: Final = "https://apis.data.go.kr/1400000"

TOP100: Final = Source(
    id="ktc",
    name="한국등산트레킹지원센터 100대명산 목록정보",
    url=KTC_BASE + "/top100FamtListBasiInfoService/getTop100FamtListBasiInfoList",
    key_param="serviceKey",
    fmt="json",
    page_size=100,
    page="https://www.data.go.kr/data/15125127/openapi.do",
    extra=(("type", "json"),),
)
POI: Final = Source(
    id="ktcPoi",
    name="한국등산트레킹지원센터 100대명산 봉우리POI정보",
    url=KTC_BASE + "/peakPoiInfoService/getPeakPoiInfoList",
    key_param="serviceKey",
    fmt="json",
    page_size=1000,
    page="https://www.data.go.kr/data/15097953/openapi.do",
    extra=(("type", "json"), ("srchPlaceTpeCd", "PEAK")),
)
PLUS: Final = Source(
    id="ktcPlus",
    name="한국등산트레킹지원센터 100대명산플러스 목록정보",
    url=KTC_BASE + "/top100FamtPlusListBasiInfoService/getTop100FamtPlusListBasiInfoList",
    key_param="serviceKey",
    fmt="json",
    page_size=100,
    page="https://www.data.go.kr/data/15125128/openapi.do",
    extra=(("type", "json"),),
)
TRAIL: Final = Source(
    id="kfsTrail",
    name="산림청 명산등산로",
    url=KFS_BASE + "/cultureInfoService/gdTrailInfoOpenAPI",
    key_param="serviceKey",
    fmt="xml",
    page_size=100,
    page="https://www.data.go.kr/data/15158915/openapi.do",
)
MNT: Final = Source(
    id="kfsMnt",
    name="산림청 산정보",
    url=KFS_BASE + "/service/cultureInfoService2/mntInfoOpenAPI2",
    key_param="ServiceKey",
    fmt="xml",
    page_size=1000,
    page="https://www.data.go.kr/data/15058662/openapi.do",
)

# 15125128 (plus) answered only timeouts on 2026-11-09; it stays off until a
# real list is saved as a fixture (research.md 사실 정정 2026-11-09, D9 → 1.1).
SOURCES: Final[Mapping[str, bool]] = {"ktc": True, "ktcPoi": True, "kfsTrail": True, "kfsMnt": True, "ktcPlus": False}

JSON_SUCCESS: Final = "0"
XML_SUCCESS: Final = "00"
# Gateway reason codes worth another try: application error, HTTP error,
# service timeout. Key, quota and "no such service" errors are not.
RETRYABLE_REASONS: Final = frozenset({"01", "04", "05"})
MAX_PAGES: Final = 20

# HTTP. Only this origin is ever requested.
API_ORIGINS: Final = frozenset({("https", "apis.data.go.kr")})
TIMEOUT_SECONDS: Final = 90.0
RETRIES: Final = 2
BACKOFF_BASE_SECONDS: Final = 5.0
MAX_API_BYTES: Final = 8 * 1024 * 1024
USER_AGENT: Final = "mountain-stamp-data-collector/1 (+https://github.com/jinhom6225/mountain-stamp-data)"

# Records.
COORD_DECIMALS: Final = 5
# Generous box around South Korea incl. Jeju, Ulleungdo and Dokdo
# (south, west, north, east); catches swapped lat/lon.
KOREA_BOX: Final = (32.0, 123.0, 39.9, 132.0)
MAX_HEIGHT_M: Final = 2000.0

# Stamp points: the weekly POI check warns when no POI of the same mountain
# lies within this distance of any stamp point; an override moving the point
# further than OVERRIDE_WARN_M from the source needs `farFromSource: true`.
POI_CHECK_M: Final = 300.0
OVERRIDE_WARN_M: Final = 2000.0
# Diff: a stamp point moving at least this far is reported.
MOVE_REPORT_M: Final = 30.0

# Run guards (템플릿 봉투 규칙): more than this share lost or invalid aborts.
MAX_DROP_RATIO: Final = 0.2
MAX_INVALID_RATIO: Final = 0.2

# Key.
KEY_ENV: Final = "DATA_GO_KR_SERVICE_KEY"
KEY_FILE: Final = Path.home() / ".secrets" / "datagokr" / "service_key"

# Repository layout.
DATA_PATH: Final = Path("data") / "mountains.json"
OVERRIDES_PATH: Final = Path("overrides") / "summits.json"

SIDO_SHORT: Final[Mapping[str, str]] = {
    "서울특별시": "서울",
    "부산광역시": "부산",
    "대구광역시": "대구",
    "인천광역시": "인천",
    "광주광역시": "광주",
    "대전광역시": "대전",
    "울산광역시": "울산",
    "세종특별자치시": "세종",
    "경기도": "경기",
    "강원특별자치도": "강원",
    "강원도": "강원",
    "충청북도": "충북",
    "충청남도": "충남",
    "전북특별자치도": "전북",
    "전라북도": "전북",
    "전라남도": "전남",
    "경상북도": "경북",
    "경상남도": "경남",
    "제주특별자치도": "제주",
}
