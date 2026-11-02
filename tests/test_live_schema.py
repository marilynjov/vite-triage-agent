"""The live-run event contract: every event the loop emits validates once numbered."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.schema import LIVE_EVENT, Failed, RunStarted
from tests.test_loop import CSS
from tests.test_loop_events import _final, _go, _tool_turn

TOTALS = {"turns": 1, "tokens": 20, "cost_usd": 0.0, "seconds": 0.1}


def test_each_loop_event_validates_once_numbered():
    events, _, _ = _go(_tool_turn(), _final([CSS]))
    assert len(events) == 7
    for i, e in enumerate(events):
        parsed = LIVE_EVENT.validate_python({**e, "seq": i})
        assert parsed.type == e["type"] and parsed.seq == i


def test_non_budget_failure_from_the_loop_validates():
    event = {"type": "failed", "reason": "api", "message": "boom", "totals": TOTALS, "seq": 3}
    parsed = LIVE_EVENT.validate_python(event)
    assert parsed.limit is None and parsed.value is None


def test_unknown_type_is_rejected():
    with pytest.raises(ValidationError):
        LIVE_EVENT.validate_python({"type": "nope", "seq": 0})


def test_extra_field_is_rejected():
    with pytest.raises(ValidationError):
        LIVE_EVENT.validate_python({"type": "step_started", "turn": 1, "seq": 0, "x": 1})


def test_failed_budget_has_limit_and_value():
    ok = Failed(type="failed", seq=1, reason="budget", message="m", totals=TOTALS,
                limit="dollars", value=0.05)
    assert ok.limit == "dollars" and ok.value == 0.05
    with pytest.raises(ValidationError):
        Failed(type="failed", seq=1, reason="budget", message="m", totals=TOTALS,
               limit="bananas", value=1)


def test_run_started_candidates():
    c = {"path": "a.ts", "score": 0.5, "start_line": 1, "end_line": 9}
    e = RunStarted(type="run_started", seq=0, candidates=[c])
    assert e.candidates[0].path == "a.ts"
