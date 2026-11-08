"""Shared test helpers: real fixtures, a fake data.go.kr server and a runner."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Union
from xml.sax.saxutils import escape

from collector import collect
from collector.config import KEY_ENV
from collector.http_client import RawResponse

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REAL = FIXTURES / "real"
HAND = FIXTURES / "hand"
REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_OVERRIDES = REPO_ROOT / "overrides" / "summits.json"

FAKE_KEY = "FAKE+KEY/abc==SECRET123"
NO_KEY_FILE = Path("/nonexistent/mountain-stamp-data/service_key")
FIRST_RUN_AT = "2026-11-10T04:10:00+09:00"
SECOND_RUN_AT = "2026-11-17T04:10:00+09:00"
# The app template envelope parser accepts UTC "Z" timestamps only.
FIRST_RUN_UTC = "2026-11-09T19:10:00Z"

Outcome = Union[RawResponse, BaseException]


def real_bytes(name: str) -> bytes:
    return (REAL / name).read_bytes()


def hand_bytes(name: str) -> bytes:
    return (HAND / name).read_bytes()


def _json_rows(name: str) -> List[dict]:
    return list(json.loads(real_bytes(name).decode("utf-8"))["response"]["body"]["items"]["item"])


def _xml_rows(name: str) -> List[dict]:
    root = ET.fromstring(real_bytes(name))
    return [{child.tag: (child.text or "") for child in item} for item in root.findall("body/items/item")]


def real_list() -> List[dict]:
    return _json_rows("ktc_top100_list.json")


def real_pois() -> List[dict]:
    return _json_rows("ktc_peak_poi_peak_p1.json")


def real_trail() -> List[dict]:
    return _xml_rows("kfs_trail_search_p1.xml")


def real_info() -> List[dict]:
    return _xml_rows("kfs_mnt_info_all_p1.xml")


def ktc_page(rows: Sequence[dict], page: int, total: int) -> bytes:
    """A B553662 JSON page in the real shape (resultCode "0")."""
    body = {"items": {"item": list(rows)} if rows else "", "numOfRows": len(rows), "pageNo": page, "totalCount": total}
    document = {"response": {"header": {"resultCode": "0", "resultMsg": "NORMAL_SERVICE"}, "body": body}}
    return json.dumps(document, ensure_ascii=False).encode("utf-8")


def kfs_page(rows: Sequence[dict], page: int, total: int) -> bytes:
    """A 산림청 XML page in the real shape (resultCode "00", `<items/>` when empty)."""
    items = "".join("<item>" + "".join(f"<{k}>{escape(v)}</{k}>" for k, v in row.items()) + "</item>" for row in rows)
    head = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response><header><resultCode>00</resultCode>'
        "<resultMsg>NORMAL SERVICE.</resultMsg></header><body>"
    )
    tail = f"<numOfRows>{len(rows)}</numOfRows><pageNo>{page}</pageNo><totalCount>{total}</totalCount></body></response>"
    return (head + (f"<items>{items}</items>" if items else "<items/>") + tail).encode("utf-8")


ENDPOINTS = {
    "/B553662/top100FamtListBasiInfoService/getTop100FamtListBasiInfoList": "list",
    "/B553662/peakPoiInfoService/getPeakPoiInfoList": "poi",
    "/1400000/cultureInfoService/gdTrailInfoOpenAPI": "trail",
    "/1400000/service/cultureInfoService2/mntInfoOpenAPI2": "info",
}


@dataclass
class FakeDataGoKr:
    """Answers the four list operations from row lists (real by default).
    `errors[kind]` replaces every answer of that operation; `sequence[kind]`
    is used first, one outcome per call."""

    rows: Dict[str, List[dict]] = field(
        default_factory=lambda: {"list": real_list(), "poi": real_pois(), "trail": real_trail(), "info": real_info()}
    )
    errors: Dict[str, Outcome] = field(default_factory=dict)
    sequence: Dict[str, List[Outcome]] = field(default_factory=dict)

    def __call__(self, url: str) -> Outcome:
        parts = urllib.parse.urlsplit(url)
        if parts.netloc != "apis.data.go.kr" or parts.path not in ENDPOINTS:
            raise AssertionError("unexpected URL " + parts.netloc + parts.path)
        kind = ENDPOINTS[parts.path]
        if self.sequence.get(kind):
            return self.sequence[kind].pop(0)
        if kind in self.errors:
            return self.errors[kind]
        query = dict(urllib.parse.parse_qsl(parts.query))
        page, size = int(query["pageNo"]), int(query["numOfRows"])
        rows = self.rows[kind]
        chunk = rows[(page - 1) * size : page * size]
        build = ktc_page if kind in ("list", "poi") else kfs_page
        return RawResponse(200, build(chunk, page, len(rows)))


class FakeTransport:
    def __init__(self, handler: Callable[[str], Outcome]) -> None:
        self.handler = handler
        self.calls: List[str] = []

    def get(self, url: str, *, operation: str, timeout: float, max_bytes: int) -> RawResponse:
        self.calls.append(url)
        outcome = self.handler(url)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def kinds(self) -> List[str]:
        return [ENDPOINTS[urllib.parse.urlsplit(url).path] for url in self.calls]


class FakeClock:
    def __init__(self) -> None:
        self.sleeps: List[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


@dataclass
class RunResult:
    code: int
    out: str
    transport: FakeTransport
    commit_message: str
    notice: str
    sleeps: List[float]

    @property
    def text(self) -> str:
        return "\n".join((self.out, self.commit_message, self.notice))


def run_collect(
    repo: Path,
    server: Optional[Callable[[str], Outcome]] = None,
    *,
    env: Optional[Dict[str, str]] = None,
    now: str = FIRST_RUN_AT,
    transport=None,
) -> RunResult:
    fake = transport or FakeTransport(server or FakeDataGoKr())
    clock = FakeClock()
    out = io.StringIO()
    message_file = repo.parent / (repo.name + "-commit.txt")
    notice_file = repo.parent / (repo.name + "-notice.txt")
    for path in (message_file, notice_file):
        if path.exists():
            path.unlink()
    code = collect.run(
        ["--repo", str(repo), "--commit-message-file", str(message_file), "--notice-file", str(notice_file)],
        env={KEY_ENV: FAKE_KEY} if env is None else env,
        transport=fake,
        sleep=clock.sleep,
        now=lambda: datetime.fromisoformat(now),
        out=out,
        key_file=NO_KEY_FILE,
    )
    read = lambda path: path.read_text(encoding="utf-8") if path.exists() else ""  # noqa: E731
    return RunResult(code, out.getvalue(), fake, read(message_file), read(notice_file), clock.sleeps)


@contextlib.contextmanager
def temp_repo(with_overrides: bool = True) -> Iterator[Path]:
    """A scratch repository, with the real overrides file by default."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        if with_overrides:
            (repo / "overrides").mkdir()
            shutil.copy(REAL_OVERRIDES, repo / "overrides" / "summits.json")
        yield repo


def snapshot(root: Path, skip: Sequence[str] = ("overrides",)) -> Dict[str, bytes]:
    """Every file under `root` except the input folders (relative path -> bytes)."""
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.relative_to(root).parts[0] not in skip
    }
