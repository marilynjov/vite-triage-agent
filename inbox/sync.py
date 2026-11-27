"""Follow the corpus's default branch, and move the pin and the index only when asked.

The index and the agent's tools both read the commit in pinned_sha.txt. For live
use that commit should be the branch's current head; `repin` writes it and
rebuilds the index (free: local embeddings), and the session asks first.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from mining import config as M

STRATEGY = "fixed_v1_header"


def _git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_path), *args], capture_output=True, text=True)


def default_branch(repo_path: Path) -> str:
    r = _git(repo_path, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    return r.stdout.strip().split("/", 1)[-1] if r.returncode == 0 and r.stdout.strip() else "main"


def branch_head(repo_path: Path, branch: str) -> str:
    fetched = _git(repo_path, "fetch", "-q", "origin", branch)
    if fetched.returncode != 0:
        raise RuntimeError(f"git fetch origin {branch} failed: {fetched.stderr.strip()}")
    return _git(repo_path, "rev-parse", f"origin/{branch}").stdout.strip()


def _reindex() -> None:
    subprocess.run([sys.executable, "-m", "retrieval.index", "--strategy", STRATEGY, "--rebuild"],
                   check=True)


def repin(sha: str, *, pin_path: Path = M.PINNED_SHA, reindex=_reindex) -> None:
    pin_path.write_text(sha + "\n")
    reindex()
