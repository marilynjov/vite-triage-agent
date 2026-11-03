"""Turn checked search/replace edits into new file contents and a unified diff."""
from __future__ import annotations

import difflib

from agent import loop, stages, tools
from agent.loop import Budget, Run
from api.schema import Edit, PatchResult

PATCH_TURNS = 6


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


def run_patch(title: str, body: str, hypothesis: dict, prefix: str, cutoff: str, *,
              budget: Budget | None = None, client=None) -> Run:
    """One loop call with every tool reading the tree at `prefix`, the commit before the fix."""
    with tools.at_rev(prefix):
        return loop.run(
            title, body, [], cutoff, system=stages.PATCH_SYSTEM, tool_specs=stages.PATCH_TOOLS,
            max_turns=PATCH_TURNS, budget=budget, client=client, output_model=PatchResult,
            check=stages.check_patch(prefix),
            prompt=stages.patch_prompt(title, body, hypothesis))
