"""Turn checked search/replace edits into new file contents and a unified diff."""
from __future__ import annotations

import difflib

from agent import tools
from api.schema import Edit


def _original(path: str, rev: str) -> str:
    return tools._git("show", f"{rev}:{path}").stdout


def apply_edits(edits: list[Edit], rev: str) -> dict[str, str]:
    """Path -> full new content. Assumes `stages.check_patch` passed: each `old` occurs once
    and no spans overlap, so applying from the end keeps earlier offsets valid."""
    out: dict[str, str] = {}
    for path in dict.fromkeys(e.path for e in edits):
        text = _original(path, rev)
        mine = sorted(((text.index(e.old), e) for e in edits if e.path == path),
                      key=lambda t: t[0], reverse=True)
        for start, e in mine:
            text = text[:start] + e.new + text[start + len(e.old):]
        out[path] = text
    return out


def patch_to_diff(edits: list[Edit], rev: str) -> str:
    diff = []
    for path, new in apply_edits(edits, rev).items():
        diff.extend(difflib.unified_diff(
            _original(path, rev).splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile=f"a/{path}", tofile=f"b/{path}"))
    return "".join(diff)
