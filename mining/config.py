"""Every knob in one place, so re-pointing at another repo is an .env edit."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
RAW = DATA / "raw"
RAW.mkdir(parents=True, exist_ok=True)

OWNER = os.getenv("REPO_OWNER", "vitejs")
NAME = os.getenv("REPO_NAME", "vite")

_repo = os.getenv("REPO_PATH") or "repos/vite"
REPO_PATH = Path(_repo) if Path(_repo).is_absolute() else ROOT / _repo

# Only files under this prefix are eligible to be gold labels. Everything else
# (docs, playground, tests, other packages) is noise for a code-retrieval task.
SCOPE_PREFIX = os.getenv("SCOPE_PREFIX", "packages/vite/src/")
SOURCE_EXTS = (".ts", ".js", ".mts", ".cts")
TEST_MARKERS = ("__tests__", ".spec.", ".test.")

MAX_GOLD_FILES = int(os.getenv("MAX_GOLD_FILES", "10"))
MIN_BODY_CHARS = int(os.getenv("MIN_BODY_CHARS", "80"))
SINCE_YEARS = float(os.getenv("SINCE_YEARS", "3"))

COMMITS_JSONL = RAW / "commits.jsonl"
ISSUES_JSONL = RAW / "issues.jsonl"
PULLS_JSONL = RAW / "pulls.jsonl"
DATASET_JSONL = DATA / "dataset.jsonl"
PINNED_SHA = DATA / "pinned_sha.txt"
SPLIT_DIR = DATA / "splits"

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")


def is_gold_candidate(path: str) -> bool:
    """A file is a usable label only if it's source code inside our scope."""
    if not path.startswith(SCOPE_PREFIX):
        return False
    if not path.endswith(SOURCE_EXTS):
        return False
    return not any(marker in path for marker in TEST_MARKERS)
