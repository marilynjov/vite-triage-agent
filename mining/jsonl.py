"""Tiny JSONL helpers. Every stage writes one, so re-running is cheap."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator


def write(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    n = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def read(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"missing {path} — run the earlier stage first")
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)
