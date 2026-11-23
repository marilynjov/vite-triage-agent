"""Nightly retrieval eval: score every val row, append one record, flag any change.

    python -m eval.nightly        # exit 0 same or first, 1 changed, 2 failed

Retrieval makes no model call, so this costs nothing and is deterministic for a
given index, pin and strategy: any metric that moves means the code or the index
changed. A record is compared with the last ok record of the same pin, strategy
and n; a new pin, strategy or n starts a new baseline. A failure (Postgres or
Ollama down) is recorded with its error and is never a baseline. Reads only the
val split.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from mining import config as M

from . import retrieve

HISTORY = M.DATA / "nightly" / "retrieve.jsonl"
STRATEGY = "fixed_v1_header"
K_VALUES = [1, 5, 10]
EPS = 1e-9

log = logging.getLogger(__name__)


def load_history(path: Path = HISTORY) -> list[dict]:
    """Every record; a line that doesn't parse (a crash or full disk mid-append) is skipped."""
    if not path.exists():
        return []
    out = []
    for i, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            log.warning("%s:%d: skipped a line that does not parse", path, i)
    return out


def _append(path: Path, rec: dict) -> None:
    """Append one line, first ending a partial last line so the record stands alone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.exists() and path.stat().st_size > 0 and not path.read_bytes().endswith(b"\n")
    with path.open("a") as f:
        f.write(("\n" if partial else "") + json.dumps(rec) + "\n")


def compare(prev: dict | None, cur: dict) -> bool:
    """True when `cur` moved from a comparable `prev` (same pin, strategy and n)."""
    if prev is None or any(prev[k] != cur[k] for k in ("pin", "strategy", "n")):
        return False
    return any(abs(prev[s].get(m, 0.0) - v) > EPS
               for s in ("all", "clean") for m, v in cur[s].items())


def _baseline(history: list[dict], pin: str, n: int) -> dict | None:
    for r in reversed(history):
        if r["ok"] and r["pin"] == pin and r["strategy"] == STRATEGY and r["n"] == n:
            return r
    return None


def run(*, evaluate=None, path: Path = HISTORY, now: datetime | None = None) -> int:
    evaluate = evaluate or retrieve.evaluate
    rec = {"at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
           "ok": True, "pin": "", "strategy": STRATEGY, "n": 0, "clean_n": 0,
           "all": {}, "clean": {}, "seconds": 0.0, "changed": False, "error": None}
    try:  # setup too: a missing pin or split is a failed night, recorded, not a crash
        rec["pin"] = M.PINNED_SHA.read_text().strip()
        rows = [json.loads(l) for l in (M.SPLIT_DIR / "val.jsonl").read_text().splitlines() if l]
        rec["n"] = len(rows)
        result = evaluate(rows, K_VALUES, STRATEGY)
        rec.update({k: result[k] for k in ("n", "clean_n", "seconds", "all", "clean")})
    except Exception as exc:  # a 3 a.m. run with the database down is a record, not a crash
        rec.update(ok=False, error=f"{type(exc).__name__}: {exc}")
    if rec["ok"]:
        rec["changed"] = compare(_baseline(load_history(path), rec["pin"], rec["n"]), rec)
    _append(path, rec)
    if not rec["ok"]:
        print(f"{rec['at']} FAILED: {rec['error']}")
        return 2
    c = rec["clean"]
    print(f"{rec['at']} {STRATEGY} n={rec['n']} clean recall@10 {c['recall@10']:.1%} "
          f"MRR {c['mrr']:.1%}" + ("  CHANGED" if rec["changed"] else ""))
    return 1 if rec["changed"] else 0


if __name__ == "__main__":
    try:
        code = run()
    except Exception as exc:  # never exit 1 ("changed") for a crash
        print(f"FAILED: {type(exc).__name__}: {exc}")
        code = 2
    sys.exit(code)
