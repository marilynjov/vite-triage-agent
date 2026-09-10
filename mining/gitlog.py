"""Stage 1 — read the local clone. No network, no rate limits.

One `git log` pass gives us every mainline commit with the files it touched.
We walk --first-parent so we see the mainline as GitHub presents it, and -m so
merge commits report their diff instead of showing nothing.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from . import config as C
from . import jsonl

# Squash-merge convention: "fix(css): handle empty url (#12345)"
SQUASH_PR = re.compile(r"\(#(\d+)\)\s*$")
# Merge-commit convention: "Merge pull request #12345 from user/branch"
MERGE_PR = re.compile(r"^Merge pull request #(\d+)", re.I)
# Closing keywords anywhere in a commit message.
CLOSES = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)", re.I
)

REC, FLD, END = "\x01", "\x02", "\x03"


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(C.REPO_PATH), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def pinned_sha() -> str:
    """The commit the whole project is evaluated against."""
    return _git("rev-parse", "HEAD").strip()


def tracked_files(sha: str) -> set[str]:
    """Every path that exists at the pinned commit."""
    out = _git("ls-tree", "-r", "--name-only", sha)
    return set(out.splitlines())


def harvest() -> Path:
    if not (C.REPO_PATH / ".git").exists():
        raise SystemExit(
            f"no clone at {C.REPO_PATH}\n"
            f"  git clone https://github.com/{C.OWNER}/{C.NAME}.git {C.REPO_PATH}"
        )

    sha = pinned_sha()
    C.PINNED_SHA.write_text(sha + "\n")

    fmt = f"{REC}%H{FLD}%ct{FLD}%s{FLD}%b{END}"
    raw = _git("log", "--first-parent", "-m", f"--format={fmt}", "--name-only")

    rows = []
    for chunk in raw.split(REC):
        if END not in chunk:
            continue
        header, _, filepart = chunk.partition(END)
        parts = header.split(FLD)
        if len(parts) < 4:
            continue
        commit_sha, ts, subject, body = parts[0], parts[1], parts[2], parts[3]
        files = [f for f in filepart.splitlines() if f.strip()]

        message = f"{subject}\n{body}"
        prs = set()
        for pattern in (SQUASH_PR, MERGE_PR):
            match = pattern.search(subject)
            if match:
                prs.add(int(match.group(1)))
        closes = {int(n) for n in CLOSES.findall(message)}

        rows.append({
            "sha": commit_sha,
            "ts": int(ts) if ts.isdigit() else 0,
            "subject": subject,
            "pr_refs": sorted(prs),
            "closes_refs": sorted(closes),
            "files": files,
        })

    n = jsonl.write(C.COMMITS_JSONL, rows)
    print(f"pinned sha       {sha}")
    print(f"commits parsed   {n:,}")
    print(f"with a PR ref    {sum(1 for r in rows if r['pr_refs']):,}")
    print(f"closing an issue {sum(1 for r in rows if r['closes_refs']):,}")
    print(f"→ {C.COMMITS_JSONL}")
    return C.COMMITS_JSONL
