"""Fail-to-pass: a patch passes when the fix's own tests pass on the pre-fix tree plus the patch."""
from __future__ import annotations

from dataclasses import dataclass

from agent.patch import apply_edits
from api.schema import Edit
from eval import container
from eval.fixes import Fix, file_at


@dataclass(frozen=True)
class VerifyResult:
    passed: bool
    exit_code: int
    seconds: float
    tail: str
    timed_out: bool
    status: str  # container.STATUSES; "infra" is never a test failure


def verify(fix: Fix, edits: list[Edit]) -> VerifyResult:
    """Run the gold tests (at the fix commit) over the pre-fix tree with `edits` applied."""
    files = {**apply_edits(edits, fix.prefix_sha),
             **{p: file_at(fix.fix_sha, p) for p in fix.gold_tests}}
    with container.LOCK:
        run = container.run_tests(fix, files)
    return VerifyResult(run.status == "pass", run.exit_code, run.seconds, run.tail,
                        run.timed_out, run.status)
