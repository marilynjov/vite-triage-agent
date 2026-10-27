"""Grounded triage: the deterministic parts. The model call itself is measured
by eval/grounded.py, not asserted here."""
from __future__ import annotations

import pytest

from api import schema as S
from api import triage
from retrieval.search import FileHit, Hit


def _hit(path: str, start: int = 1, end: int = 40, text: str = "code") -> FileHit:
    h = Hit(path, 0.8, start, end, text)
    return FileHit(path, 0.8, h)


def _grounded(files: list[str]) -> S.GroundedTriageResult:
    return S.GroundedTriageResult(
        reasoning="r", issue_type="bug", severity="medium",
        suspected_components=[], reproduction_steps=[], confidence=0.5,
        suspected_files=files,
    )


def test_files_outside_the_candidate_list_are_a_contract_violation():
    with pytest.raises(ValueError, match="not.ts"):
        triage.check_candidates(_grounded(["a.ts", "not.ts"]), [_hit("a.ts")])


def test_files_from_the_candidate_list_pass():
    triage.check_candidates(_grounded(["b.ts", "a.ts"]), [_hit("a.ts"), _hit("b.ts")])


def test_prompt_lists_candidates_in_rank_order_with_line_ranges():
    prompt = triage.grounded_prompt(
        "Less @import fails", "body text",
        [_hit("node/plugins/css.ts", 10, 52), _hit("node/config.ts", 1, 30)],
    )
    assert "Less @import fails" in prompt and "body text" in prompt
    assert prompt.index("node/plugins/css.ts:10-52") < prompt.index("node/config.ts:1-30")
