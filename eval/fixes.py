"""The real fix for a row: the commit before it, its unit tests, its source changes.

A row's fix is the first of its fix commits (in commits.jsonl order) that adds or
changes a unit test under packages/vite/src. Its first parent is the pre-fix
commit verify builds on. Tests come from the fix commit ("gold tests"); the
fix's own source changes are kept so the harness can check the real fix passes.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

import mining.config as M
from eval.gold_regions import fix_commits

SRC = "packages/vite/src/"
GIT_TIMEOUT = 60
_SPEC = re.compile(r"\.(spec|test)\.[tj]s$")


@dataclass(frozen=True)
class Fix:
    issue: int
    fix_sha: str
    prefix_sha: str
    gold_tests: tuple[str, ...]
    test_paths: tuple[str, ...]
    fix_source: tuple[str, ...]
    fix_deleted: tuple[str, ...]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(M.REPO_PATH), *args],
        capture_output=True, text=True, errors="replace", check=False, timeout=GIT_TIMEOUT,
    )


def _ok(*args: str) -> str:
    r = _git(*args)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def file_at(sha: str, path: str) -> str:
    return _ok("show", f"{sha}:{path}")


def _is_test(path: str) -> bool:
    return path.startswith(SRC) and "/__tests__/" in path


def _fix_at(issue: int, sha: str) -> Fix | None:
    prefix = _ok("rev-parse", f"{sha}^1").strip()
    gold, source, deleted = [], [], []
    for line in _ok("diff", "--name-status", "--no-renames", prefix, sha).splitlines():
        status, path = line.split("\t", 1)
        if not path.startswith(SRC):
            continue
        if status == "D":
            deleted.append(path)
        elif status in ("A", "M"):
            (gold if _is_test(path) else source).append(path)
    tests = tuple(p for p in gold if _SPEC.search(p))
    if not tests:
        return None
    return Fix(issue, sha, prefix, tuple(gold), tests, tuple(source), tuple(deleted))


def fix_for(row: dict, commits: list[dict]) -> Fix | None:
    """The first fix commit that changes a unit test, or None when none does."""
    for sha in fix_commits(row, commits):
        fix = _fix_at(row["issue"], sha)
        if fix:
            return fix
    return None
