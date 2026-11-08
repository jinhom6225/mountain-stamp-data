"""One collector run: 100대 명산 list (+ POI, 명산등산로, 산정보) -> checks ->
overrides -> data/mountains.json.

Usage (from the repository root):
    python3 -m collector.collect [--commit-message-file PATH] [--notice-file PATH]

Exit codes: 0 written (warnings possible), 1 aborted by a run guard (nothing
written), 2 configuration error (no key, broken overrides file), 3 the
mountain list call failed (nothing written), 4 unexpected.
A failed POI / 명산등산로 / 산정보 call is a warning: the run goes on and keeps
the previous text of each mountain.
The key comes from DATA_GO_KR_SERVICE_KEY, else ~/.secrets/datagokr/service_key;
it is never printed or written.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Sequence, TextIO, Tuple, TypeVar

from . import guard
from .config import KEY_ENV, KEY_FILE, KST, MNT, OVERRIDES_PATH, POI, SOURCES, TOP100, TRAIL, Source
from .diff import Diff, compare
from .errors import CallFailed, ConfigError, RunAborted
from .http_client import ApiClient, Transport, UrllibTransport
from .merge import Reuse, build_item, join_info, join_trail, labels, mountain_id, override_warnings, poi_warnings
from .output import Document, Previous, document, load_previous, write
from .overrides import Override, load_overrides
from .pages import fetch_all
from .records import (
    ListMountain,
    MountainInfo,
    Poi,
    RecordInvalid,
    TrailInfo,
    parse_list_mountain,
    parse_mountain_info,
    parse_poi,
    parse_trail_info,
)

EXIT_OK, EXIT_ABORTED, EXIT_CONFIG, EXIT_CALL, EXIT_UNEXPECTED = 0, 1, 2, 3, 4
NOTICE_TITLE = "mountain-stamp-data 수집"
WARNING_LIMIT = 8
Log = Callable[[str], None]
R = TypeVar("R")


@dataclass(frozen=True)
class Options:
    repo: Path
    commit_message_file: Optional[Path]
    notice_file: Optional[Path]


@dataclass(frozen=True)
class Parsed:
    rows: Tuple[object, ...]
    raw_count: int
    invalid: int


@dataclass(frozen=True)
class Fetched:
    mountains: Tuple[ListMountain, ...]
    raw_count: int
    invalid: int
    pois: Optional[Tuple[Poi, ...]]
    trail: Optional[Tuple[TrailInfo, ...]]
    info: Optional[Tuple[MountainInfo, ...]]

    @property
    def failed(self) -> Tuple[str, ...]:
        parts = (("ktcPoi", self.pois), ("kfsTrail", self.trail), ("kfsMnt", self.info))
        return tuple(sid for sid, value in parts if value is None and SOURCES.get(sid))


@dataclass(frozen=True)
class Built:
    data: Document
    diff: Diff
    total: int
    overridden: int
    joined_trail: int
    joined_info: int
    no_poi: int
    warnings: Tuple[str, ...]


def parse_args(argv: Sequence[str]) -> Options:
    parser = argparse.ArgumentParser(prog="collector.collect", description="Collect 100 mountain data.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root (default: current dir)")
    parser.add_argument("--commit-message-file", type=Path, help="write the commit message here on success")
    parser.add_argument("--notice-file", type=Path, help="write the Telegram notice text here")
    args = parser.parse_args(list(argv))
    return Options(args.repo, args.commit_message_file, args.notice_file)


def load_key(env: Mapping[str, str], key_file: Optional[Path]) -> str:
    key = (env.get(KEY_ENV) or "").strip()
    if not key and key_file is not None:
        try:
            key = key_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            key = ""
        except OSError:
            raise ConfigError("service key file unreadable") from None
    if not key:
        raise ConfigError(f"{KEY_ENV} is not set")
    if any(ch.isspace() or ord(ch) < 32 for ch in key):
        raise ConfigError("service key has whitespace or control characters")
    return key


def key_scrubber(key: str, out: TextIO) -> Log:
    """A logger that never prints the key or its URL-encoded forms."""
    forms = sorted({key, urllib.parse.quote(key, safe=""), urllib.parse.quote_plus(key)}, key=len, reverse=True)

    def log(line: str) -> None:
        for form in forms:
            line = line.replace(form, "***")
        print(line, file=out)

    return log


def parse_rows(source: Source, rows: Sequence[object], parse: Callable[[object], R], log: Log) -> Tuple[List[R], int]:
    """Valid records and the number of invalid rows (warned, never fatal here)."""
    records: List[R] = []
    invalid = 0
    for position, raw in enumerate(rows, start=1):
        try:
            records.append(parse(raw))
        except RecordInvalid as error:
            invalid += 1
            log(f"warning: {source.id} row #{position}: {error}")
    return records, invalid


def fetch_mountains(client: ApiClient, log: Log) -> Tuple[List[ListMountain], int, int]:
    """The 100대 명산 list. A failed call raises (the run stops)."""
    rows = fetch_all(client, TOP100)
    mountains, invalid = parse_rows(TOP100, rows, parse_list_mountain, log)
    unique: Dict[str, ListMountain] = {}
    for mountain in mountains:
        if mountain.frtrl_id in unique:
            invalid += 1
            log(f"warning: ktc duplicate frtrlId {mountain.frtrl_id}")
            continue
        unique[mountain.frtrl_id] = mountain
    log(f"fetched: ktc rows={len(rows)} invalid={invalid}")
    return list(unique.values()), len(rows), invalid


def fetch_optional(client: ApiClient, source: Source, parse: Callable[[object], R], log: Log) -> Optional[Tuple[R, ...]]:
    """Rows of a supporting source, or None when it is off or failed."""
    if not SOURCES.get(source.id):
        return None
    try:
        rows = fetch_all(client, source)
    except CallFailed as error:
        log(f"warning: {source.id} failed ({error.describe()}); previous text kept")
        return None
    records, invalid = parse_rows(source, rows, parse, log)
    log(f"fetched: {source.id} rows={len(rows)} invalid={invalid}")
    return tuple(records)


def fetch_sources(client: ApiClient, log: Log) -> Fetched:
    mountains, raw_count, invalid = fetch_mountains(client, log)
    return Fetched(
        mountains=tuple(sorted(mountains, key=lambda m: (m.name, m.qualifier or "", m.frtrl_id))),
        raw_count=raw_count,
        invalid=invalid,
        pois=fetch_optional(client, POI, parse_poi, log),
        trail=fetch_optional(client, TRAIL, parse_trail_info, log),
        info=fetch_optional(client, MNT, parse_mountain_info, log),
    )


def _source_entry(source: Source, count: int, generated_at: str, extracted: Optional[str] = None) -> Document:
    entry: Document = {"id": source.id, "name": source.name, "url": source.page, "count": count, "fetchedAt": generated_at}
    if extracted:
        entry["extractedAt"] = extracted
    return entry


def sources_list(fetched: Fetched, previous: Previous, generated_at: str) -> List[Document]:
    """This run's sources; a failed one keeps its previous entry (older
    `fetchedAt`), so the app can show when that text was last refreshed."""
    extracted = Counter(m.extracted for m in fetched.mountains).most_common(1)
    entries = [_source_entry(TOP100, len(fetched.mountains), generated_at, extracted[0][0] if extracted else None)]
    for source, rows in ((POI, fetched.pois), (TRAIL, fetched.trail), (MNT, fetched.info)):
        if rows is not None:
            entries.append(_source_entry(source, len(rows), generated_at))
        elif source.id in previous.sources:
            entries.append(previous.sources[source.id])
    return entries


def build_outputs(fetched: Fetched, previous: Previous, overrides: Mapping[str, Override], generated_at: str) -> Built:
    mountains = fetched.mountains
    trail_by_id = join_trail(mountains, fetched.trail or ())
    info_by_id = join_info(mountains, trail_by_id, fetched.info or ())
    names = labels(mountains)
    reuse = Reuse(text=fetched.trail is None or fetched.info is None, updated=fetched.info is None)
    items = [
        build_item(
            m,
            names[mountain_id(m)],
            overrides.get(mountain_id(m)),
            trail_by_id.get(mountain_id(m)),
            info_by_id.get(mountain_id(m)),
            previous.items.get(mountain_id(m)),
            reuse,
        )
        for m in mountains
    ]
    warnings = override_warnings(mountains, overrides)
    no_poi = 0
    if fetched.pois is not None:
        poi_lines, no_poi = poi_warnings(mountains, overrides, fetched.pois)
        warnings += poi_lines
    if fetched.trail is not None:
        warnings += [f"kfsTrail: no row for {mountain_id(m)} ({m.source_name})" for m in mountains if mountain_id(m) not in trail_by_id]
    return Built(
        data=document(generated_at, sources_list(fetched, previous, generated_at), items),
        diff=compare(previous.items, items),
        total=len(items),
        overridden=sum(1 for item in items if item["stampSource"] == "override"),
        joined_trail=len(trail_by_id),
        joined_info=len(info_by_id),
        no_poi=no_poi,
        warnings=tuple(warnings),
    )


def success_notice(built: Built, fetched: Fetched, size: int) -> str:
    lines = [
        f"✅ {NOTICE_TITLE}",
        f"산 {built.total} · 검증 탈락 {fetched.invalid} · 도장 지점 보정 {built.overridden} · {size / 1024:.0f}KB",
        built.diff.counts_text(),
    ]
    if fetched.failed:
        lines.append("⚠ 실패한 원천 (이전 글 유지): " + ", ".join(fetched.failed))
    if built.warnings:
        lines.append(f"⚠ 경고 {len(built.warnings)}")
        lines.extend("- " + w for w in built.warnings[:WARNING_LIMIT])
        if len(built.warnings) > WARNING_LIMIT:
            lines.append(f"- … {len(built.warnings) - WARNING_LIMIT}개 더 (실행 기록)")
    return "\n".join(lines) + "\n"


def collect_once(
    options: Options,
    key: str,
    transport: Transport,
    sleep: Callable[[float], None],
    now: Callable[[], datetime],
    log: Log,
) -> Tuple[str, str]:
    """Runs and writes; returns (commit message, notice). Raises on abort."""
    # UTC with "Z": the app template envelope parser rejects offsets like +09:00.
    generated_at = now().astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    overrides = load_overrides(options.repo / OVERRIDES_PATH)
    previous = load_previous(options.repo)
    if previous.warning:
        log(f"warning: {previous.warning}")
    fetched = fetch_sources(ApiClient(transport, key, sleep), log)
    guard.check_invalid(fetched.raw_count, fetched.invalid)
    guard.check_drop(len(previous.items), len(fetched.mountains))
    built = build_outputs(fetched, previous, overrides, generated_at)
    size = write(options.repo, built.data)
    for warning in built.warnings:
        log(f"warning: {warning}")
    log(
        f"summary: items={built.total} invalid={fetched.invalid} overridden={built.overridden} "
        f"joinedTrail={built.joined_trail} joinedInfo={built.joined_info} noPoi={built.no_poi} "
        f"failed={','.join(fetched.failed) or '-'} bytes={size} {built.diff.counts_text()}"
    )
    return built.diff.commit_message(built.total, built.overridden), success_notice(built, fetched, size)


def _write(path: Optional[Path], text: str) -> None:
    if path is not None:
        path.write_text(text, encoding="utf-8")


def run(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    transport: Transport,
    sleep: Callable[[float], None],
    now: Callable[[], datetime],
    out: TextIO,
    key_file: Optional[Path],
) -> int:
    options = parse_args(argv)
    log: Log = lambda line: print(line, file=out)  # noqa: E731
    keep = "이전 자료 유지"
    try:
        key = load_key(env, key_file)
        log = key_scrubber(key, out)
        message, notice = collect_once(options, key, transport, sleep, now, log)
    except ConfigError as error:
        code, notice = EXIT_CONFIG, f"❌ {NOTICE_TITLE} 설정 오류: {error} — {keep}\n"
        log(f"config error: {error}")
    except CallFailed as error:
        code, notice = EXIT_CALL, f"❌ {NOTICE_TITLE} 실패: 산 목록 API 호출 실패 ({error.describe()}) — {keep}\n"
        log(f"call failed: {error.describe()}")
    except RunAborted as error:
        code, notice = EXIT_ABORTED, f"❌ {NOTICE_TITLE} 중단 (20% 규칙): {error} — {keep}\n"
        log(f"aborted: {error}")
    except Exception as error:  # noqa: BLE001 — print the type only: messages may carry URLs
        code, notice = EXIT_UNEXPECTED, f"❌ {NOTICE_TITLE} 예기치 않은 오류 ({type(error).__name__}) — {keep}\n"
        log(f"unexpected error ({type(error).__name__})")
    else:
        code = EXIT_OK
        _write(options.commit_message_file, message)
    _write(options.notice_file, notice)
    return code


def main() -> int:
    return run(
        sys.argv[1:],
        env=os.environ,
        transport=UrllibTransport(),
        sleep=time.sleep,
        now=lambda: datetime.now(KST),
        out=sys.stdout,
        key_file=KEY_FILE,
    )


if __name__ == "__main__":
    sys.exit(main())
