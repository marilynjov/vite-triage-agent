"""Gold regions: the lines a row's real fix wrote that still survive at the pin.

A row's fix commits come from data/raw/commits.jsonl. `git blame` at the pinned
SHA names, for every surviving line, the commit that last wrote it, in pin line
numbers; the lines blamed to a fix commit are the fix's own. Contiguous lines
form one inclusive range. A file whose fix lines were deleted or rewritten
later (or a file absent at the pin) maps to [].
"""
from __future__ import annotations

import os
import re
import subprocess

import mining.config as M
from mining import jsonl

CACHE = M.DATA / "gold_regions.jsonl"
GIT_TIMEOUT = 60

Regions = dict[str, list[list[int]]]
_HEADER = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)(?: \d+)?$")


def fix_commits(row: dict, commits: list[dict]) -> list[str]:
    prs = set(row.get("linked_prs") or [])
    issue = row["issue"]
    return [
        c["sha"] for c in commits
        if prs & set(c.get("pr_refs") or []) or issue in (c.get("closes_refs") or [])
    ]


def _pin() -> str:
    return M.PINNED_SHA.read_text().strip()


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "--literal-pathspecs", "-C", str(M.REPO_PATH), *args],
        capture_output=True, text=True, errors="replace", check=False,
        timeout=GIT_TIMEOUT,
    )


def _blame(path: str) -> list[tuple[str, int]]:
    """(sha, pin line number) for every line of `path` at the pin.

    Only a path absent at the pin gives []; any other git failure (bad repo
    path, pin missing from the clone) raises, so it is never cached as empty.
    """
    pin = _pin()
    commit = _git("cat-file", "-e", f"{pin}^{{commit}}")
    if commit.returncode != 0:
        raise RuntimeError(f"git cannot resolve pin {pin} in {M.REPO_PATH}: {commit.stderr.strip()}")
    if _git("cat-file", "-e", f"{pin}:{path}").returncode != 0:
        return []
    r = _git("blame", "--line-porcelain", pin, "--", path)
    if r.returncode != 0:
        raise RuntimeError(f"git blame failed for {path} at {pin}: {r.stderr.strip()}")
    out = []
    for line in r.stdout.split("\n"):
        m = _HEADER.match(line)
        if m:
            out.append((m.group(1), int(m.group(2))))
    return out


def _group(lines: list[int]) -> list[list[int]]:
    ranges: list[list[int]] = []
    for n in sorted(set(lines)):
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1][1] = n
        else:
            ranges.append([n, n])
    return ranges


def regions_for(row: dict, commits: list[dict]) -> Regions:
    fixes = set(fix_commits(row, commits))
    out: Regions = {}
    for path in row["gold_files"]:
        mine = [n for sha, n in _blame(path) if sha in fixes] if fixes else []
        out[path] = _group(mine)
    return out


def load_regions(rows: list[dict]) -> dict[int, Regions]:
    """Regions by issue; computed once per issue and pin, cached in CACHE."""
    pin = _pin()
    cached = {}
    if CACHE.exists():
        cached = {r["issue"]: r["regions"] for r in jsonl.read(CACHE) if r.get("pin") == pin}
    todo = [r for r in rows if r["issue"] not in cached]
    if todo:
        commits = list(jsonl.read(M.RAW / "commits.jsonl"))
        for r in todo:
            cached[r["issue"]] = regions_for(r, commits)
        tmp = CACHE.with_name(CACHE.name + ".tmp")
        jsonl.write(tmp, ({"issue": i, "pin": pin, "regions": g} for i, g in cached.items()))
        os.replace(tmp, CACHE)
    return {r["issue"]: cached[r["issue"]] for r in rows}
