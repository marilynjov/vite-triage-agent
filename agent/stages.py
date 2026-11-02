"""Stage boundary checks for locate -> understand -> hypothesize.

Each factory returns a check for agent.loop.run: it raises ValueError, prefixed
with the stage name, when a stage's output breaks its contract.
"""
from __future__ import annotations

from collections.abc import Callable

import mining.config as M
from agent import tools
from api.schema import HypothesisResult, LocateResult, UnderstandResult


def _lines_ok(path: str, start: int, end: int) -> bool:
    n = tools.file_length(path)
    return n is not None and 1 <= start <= end <= n


def check_locate() -> Callable[[LocateResult], None]:
    def check(result: LocateResult) -> None:
        tracked = tools._tracked()
        bad = [f.path for f in result.files
               if f.path not in tracked or not M.is_gold_candidate(f.path)]
        if bad:
            raise ValueError(f"locate: files not in scope at the pinned tree: {', '.join(bad)}")
    return check


def check_understand(located: LocateResult) -> Callable[[UnderstandResult], None]:
    allowed = {f.path for f in located.files}

    def check(result: UnderstandResult) -> None:
        for r in result.regions:
            if r.path not in allowed:
                raise ValueError(f"understand: region file {r.path} was not located")
            if not _lines_ok(r.path, r.start_line, r.end_line):
                raise ValueError(
                    f"understand: lines {r.start_line}-{r.end_line} outside {r.path} "
                    f"(1-{tools.file_length(r.path)})")
    return check


def check_hypothesis(located: LocateResult,
                     understood: UnderstandResult) -> Callable[[HypothesisResult], None]:
    allowed = {r.path for r in understood.regions}

    def check(result: HypothesisResult) -> None:
        try:
            tools.check_in_scope(result)
        except ValueError as e:
            raise ValueError(f"hypothesize: {e}") from None
        f = result.fault
        if f.path not in allowed:
            raise ValueError(f"hypothesize: fault file {f.path} is not in an understood region")
        if not _lines_ok(f.path, f.start_line, f.end_line):
            raise ValueError(
                f"hypothesize: fault lines {f.start_line}-{f.end_line} outside {f.path} "
                f"(1-{tools.file_length(f.path)})")
    return check
