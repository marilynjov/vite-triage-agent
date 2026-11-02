"""Stage boundary checks for locate -> understand -> hypothesize.

Each factory returns a check for agent.loop.run: it raises ValueError, prefixed
with the stage name, when a stage's output breaks its contract.
"""
from __future__ import annotations

from collections.abc import Callable

import mining.config as M
from agent import tools
from api.schema import HypothesisResult, LocateResult, UnderstandResult
from api.triage import STRUCTURED_SYSTEM


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


# ---- prompts and tools ----------------------------------------------------

_TOOLS_NOTE = """
**Tools**

You have read-only tools over the repository at the commit the candidates were
indexed from: `grep(pattern, path)`, `read_file(path, start_line, end_line)`
(at most 400 lines per call), `list_dir(path)` and `git_log(path, n)`.
"""

_EVIDENCE = """
**Evidence rules**

Use only paths that exist in the repository, exactly as a tool or the candidate
list shows them; never invent a path. Claim nothing beyond what you have read.
"""

LOCATE_SYSTEM = """\
You are the first of three stages in triaging an issue for the Vite repository.
Your single job: find the files the fix for this report would touch, and say
what you saw in each. You are given the report and candidate files ranked by
text similarity, which cannot tell a file that mentions the same words from the
file that contains the fault. The candidates are a starting point, not a
boundary. The shortlist you return is all the next stage reads, so favour
recall over order: include a file when it may be where the fix goes. Return one
to five files. Do not decide where the fault is and do not triage the report.
""" + _TOOLS_NOTE + _EVIDENCE

UNDERSTAND_SYSTEM = """\
You are the second of three stages in triaging an issue for the Vite
repository. Your single job: read the files the previous stage located and name
the regions (a function or a span of lines) that implement the behaviour the
report describes. For each region give its path, symbol if any, exact line
range, what it does, and how it relates to the report. Do not judge the fault
yet and do not triage the report. Regions must be in the located files, and
their line numbers must come from what you read.
""" + _TOOLS_NOTE + _EVIDENCE

HYPOTHESIZE_SYSTEM = STRUCTURED_SYSTEM + """
**Your stage**

You are the last of three stages. Earlier stages located files and named the
regions that implement the reported behaviour; their results are given after
the report as JSON. You have no tools. The regions were read from the code by the previous stage,
so they count as evidence alongside the report, despite the statement above that
you have not read the code. From those regions alone, decide where
the fault is and how it causes the report: fill `fault` with a path and line
range inside one of the given regions, and its mechanism. Then rank the files
the fix would change in `suspected_files`, most likely first, and fill the
other triage fields. Use paths exactly as given, claim nothing beyond the
regions you were shown, and do not claim to have read anything else.
"""

LOCATE_TOOLS = tools.TOOL_SPECS
UNDERSTAND_TOOLS = [t for t in tools.TOOL_SPECS if t["name"] in ("read_file", "grep")]
HYPOTHESIZE_TOOLS: list[dict] = []


def understand_prompt(title: str, body: str, located: LocateResult) -> str:
    files = "\n".join(f"- {f.path}: {f.why}" for f in located.files)
    return (f"Title: {title}\n\n{body}\n\n---\n\nLocated files (from the previous stage):\n\n"
            f"{files}")


def hypothesize_prompt(title: str, body: str, located: LocateResult,
                       understood: UnderstandResult) -> str:
    return (f"Title: {title}\n\n{body}\n\n---\n\nLocated files:\n\n"
            f"{located.model_dump_json(indent=2)}\n\n---\n\nUnderstood regions:\n\n"
            f"{understood.model_dump_json(indent=2)}")
