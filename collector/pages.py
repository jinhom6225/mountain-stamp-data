"""List pages of the five APIs: decoding and paging.

Real-response facts (fixtures in tests/fixtures/real, research.md 사실 정정
2026-11-09):
- 한국등산트레킹지원센터 (B553662) answers JSON with `resultCode` "0";
- 산림청 (1400000) answers XML only, `resultCode` "00", an empty result is
  `<items/>` with `totalCount` 0 and blank fields are a single space;
- gateway errors are `OpenAPI_ServiceResponse/cmmMsgHeader` with a
  `returnReasonCode` — JSON for B553662, XML for 1400000. A service timeout
  ("05") comes with HTTP 504.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .config import JSON_SUCCESS, MAX_PAGES, RETRYABLE_REASONS, XML_SUCCESS, Source
from .errors import CallFailed
from .http_client import ApiClient, Params

Row = Dict[str, Any]
_SAFE_CODE = re.compile(r"^[0-9A-Za-z_]{1,40}$")
_GATEWAY = b"OpenAPI_ServiceResponse"


@dataclass(frozen=True)
class Page:
    items: Tuple[Row, ...]
    total_count: Optional[int]


def _safe_code(value: Any) -> str:
    text = str(value).strip()
    return text if _SAFE_CODE.match(text) else "?"


def _text(body: bytes, operation: str) -> str:
    if body.startswith(b"\xef\xbb\xbf"):
        body = body[3:]
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise CallFailed(operation, "body not UTF-8", retryable=False) from None


def _reason_code(text: str) -> Optional[str]:
    """`returnReasonCode` of a gateway error body (JSON or XML), if any."""
    match = re.search(r'returnReasonCode"?\s*[:>]\s*"?\s*([0-9]{1,6})', text)
    return match.group(1) if match else None


def _check_gateway(operation: str, status: int, body: bytes, text: str) -> None:
    if _GATEWAY in body[:400]:
        code = _reason_code(text)
        raise CallFailed(
            operation,
            "gateway error",
            status=status if status != 200 else None,
            result_code=code,
            retryable=code in RETRYABLE_REASONS,
        )
    if status != 200:
        raise CallFailed(operation, "HTTP error", status=status, retryable=status >= 500)


def _result_failed(operation: str, code: Any) -> CallFailed:
    safe = _safe_code(code)
    return CallFailed(operation, "result code", result_code=safe, retryable=safe in RETRYABLE_REASONS)


def _total(operation: str, value: Any) -> Optional[int]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        raise CallFailed(operation, "bad totalCount", retryable=False) from None


def _json_page(operation: str, text: str) -> Page:
    if not text.lstrip().startswith("{"):
        raise CallFailed(operation, "not JSON", retryable=False)
    try:
        document = json.loads(text, strict=False)
    except ValueError:
        raise CallFailed(operation, "broken JSON") from None
    response = document.get("response") if isinstance(document, dict) else None
    if not isinstance(response, dict):
        raise CallFailed(operation, "no response object", retryable=False)
    header = response.get("header") if isinstance(response.get("header"), dict) else {}
    if str(header.get("resultCode")) != JSON_SUCCESS:
        raise _result_failed(operation, header.get("resultCode"))
    body = response.get("body")
    if not isinstance(body, dict):
        return Page((), None)
    raw = body.get("items")
    item = raw.get("item") if isinstance(raw, dict) else raw
    if item in ("", None):
        rows: List[Any] = []
    elif isinstance(item, dict):
        rows = [item]
    elif isinstance(item, list):
        rows = item
    else:
        raise CallFailed(operation, "unexpected items", retryable=False)
    if not all(isinstance(row, dict) for row in rows):
        raise CallFailed(operation, "unexpected items", retryable=False)
    return Page(tuple(rows), _total(operation, body.get("totalCount")))


def _xml_page(operation: str, body: bytes) -> Page:
    head = body[:2048].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in body.upper():
        raise CallFailed(operation, "XML with DOCTYPE refused", retryable=False)
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise CallFailed(operation, "broken XML") from None
    if root.tag != "response":
        raise CallFailed(operation, "no response element", retryable=False)
    code = (root.findtext("header/resultCode") or "").strip()
    if code != XML_SUCCESS:
        raise _result_failed(operation, code)
    rows = tuple({child.tag: (child.text or "") for child in item} for item in root.findall("body/items/item"))
    return Page(rows, _total(operation, root.findtext("body/totalCount")))


def decode_page(source: Source, status: int, body: bytes) -> Page:
    text = _text(body, source.id)
    _check_gateway(source.id, status, body, text)
    if source.fmt == "json":
        return _json_page(source.id, text)
    return _xml_page(source.id, body)


def fetch_all(client: ApiClient, source: Source, max_pages: int = MAX_PAGES) -> List[Row]:
    """Every row, page by page until `totalCount` rows or an empty page; an
    empty page before `totalCount` means the list is incomplete (failure)."""
    rows: List[Row] = []
    total: Optional[int] = None
    for page_no in range(1, max_pages + 1):
        params: Params = source.extra + (("numOfRows", str(source.page_size)), ("pageNo", str(page_no)))
        page = client.call(source.url, source.id, params, source.key_param, lambda status, body: decode_page(source, status, body))
        total = page.total_count if page.total_count is not None else total
        rows.extend(page.items)
        if total is not None and len(rows) >= total:
            if len(rows) > total:
                raise CallFailed(source.id, f"more rows than totalCount ({len(rows)} of {total})", retryable=False)
            return rows
        if not page.items:
            if total is not None and len(rows) != total:
                raise CallFailed(source.id, f"incomplete pages ({len(rows)} of {total})", retryable=False)
            return rows
    raise CallFailed(source.id, "too many pages", retryable=False)
