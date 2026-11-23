"""The test split's seal: it opens once, for the runs declared here, at a known commit.

Val is what everything was tuned on; test is read once, at the end (Phase 07b), so
its numbers are evidence rather than another tuning set. `eval.ablation --split
test --open-test` asks `open_test(name)` before it reads test.jsonl:

- only the two runs this phase declared may open it;
- the first opening of a run must come from a clean working tree, and the commit
  is recorded, so the number is tied to the exact code that produced it;
- the same name may open it again only to resume a crashed run, and keeps its
  first commit. A new name is refused, so a disappointing number cannot be
  rerun under another name until it looks better.

The record is data/splits/TEST_OPENED.json, committed.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from mining import config as M

TEST_RUNS = ("test-final", "test-staged-n40")
SEAL = M.SPLIT_DIR / "TEST_OPENED.json"


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(M.ROOT), *args], capture_output=True,
                          text=True, check=True).stdout


def _tree_is_clean() -> bool:
    return _git("status", "--porcelain") == ""


def _head_sha() -> str:
    return _git("rev-parse", "HEAD").strip()


def open_test(name: str, *, path: Path = SEAL, clean=_tree_is_clean, head=_head_sha,
              now: str | None = None) -> dict:
    """Allow run `name` to read the test split, or exit with the reason it may not."""
    if name not in TEST_RUNS:
        raise SystemExit(f"{name!r} is not a declared test run; the test split opens only for "
                         f"{', '.join(TEST_RUNS)} (eval/seal.py)")
    opened = json.loads(path.read_text()) if path.exists() else {}
    if name in opened:  # resuming the same run, as first opened
        return opened[name]
    if not clean():
        raise SystemExit("uncommitted changes: commit first, so the test number is tied to a commit")
    entry = {"opened_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "commit": head()}
    opened[name] = entry
    path.write_text(json.dumps(opened, indent=2) + "\n")
    return entry
