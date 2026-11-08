"""`data/mountains.json` (research.md §6-2 계약): envelope, previous copy and
an atomic write. The file is rewritten every successful run so
`generatedAt` stays fresh for the app's 14-day stale rule; nothing is
written when a run is aborted."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .config import DATA_PATH, SCHEMA_VERSION

Document = Dict[str, Any]


@dataclass(frozen=True)
class Previous:
    items: Dict[str, Document]
    sources: Dict[str, Document]
    warning: Optional[str]


def document(generated_at: str, sources: Sequence[Document], items: Sequence[Document]) -> Document:
    return {"schemaVersion": SCHEMA_VERSION, "generatedAt": generated_at, "sources": list(sources), "items": list(items)}


def _by_id(rows: Any, what: str) -> Dict[str, Document]:
    if not isinstance(rows, list):
        raise ValueError(what)
    result: Dict[str, Document] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError(what)
        result[row["id"]] = row
    return result


def load_previous(repo: Path) -> Previous:
    path = repo / DATA_PATH
    if not path.exists():
        return Previous({}, {}, None)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("document")
        items = _by_id(data.get("items"), "items")
        sources = _by_id(data.get("sources", []), "sources")
    except (OSError, ValueError):
        return Previous({}, {}, "data/mountains.json unreadable; treated as a first run")
    return Previous(items, sources, None)


def dumps(data: Mapping[str, Any]) -> str:
    """One field per line: weekly diffs stay readable on GitHub; gzip on
    the raw URL removes most of the extra size."""
    return json.dumps(data, ensure_ascii=False, indent=1) + "\n"


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
        os.replace(temp_name, path)
    except BaseException:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def write(repo: Path, data: Mapping[str, Any]) -> int:
    """Writes `data/mountains.json`; returns its size in bytes."""
    text = dumps(data)
    write_text_atomic(repo / DATA_PATH, text)
    return len(text.encode("utf-8"))
