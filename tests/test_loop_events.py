"""Loop events and four-limit budgets, driven by the scripted FakeClient. No API call."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from agent import loop
from tests.test_loop import CSS, CUTOFF, FakeClient, _ApiError, _final, _hit, _tool


def _resp(content, stop="tool_use", inp=10, out=10):
    usage = NS(input_tokens=inp, output_tokens=out,
               cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return NS(content=content, stop_reason=stop, usage=usage,
              model="claude-haiku-5-5", _request_id="req_x")


def _tool_turn(id_="t1", **kw):
    return _resp([_tool(id_, "list_dir", path="packages")], **kw)


def _budget(**kw):
    base = dict(max_turns=12, max_tokens=10**9, max_seconds=10**9, max_dollars=10**9)
    base.update(kw)
    return loop.Budget(**base)


def _go(*responses, budget=None, **kw):
    events = []
    client = FakeClient(*responses)
    try:
        out = loop.run("t", "b", [_hit()], CUTOFF, client=client,
                       budget=budget or _budget(), on_event=events.append, **kw)
        return events, out, None
    except loop.RunError as exc:
        return events, None, exc


def test_two_turn_run_emits_the_exact_sequence():
    events, _, _ = _go(_tool_turn(), _final([CSS]))
    assert [e["type"] for e in events] == [
        "step_started", "usage", "tool_called", "tool_returned",
        "step_started", "usage", "done"]
    called, returned = events[2], events[3]
    assert called["tool_use_id"] == returned["tool_use_id"] == "t1"
    usages = [e for e in events if e["type"] == "usage"]
    assert [u["totals"]["turns"] for u in usages] == [1, 2]
    assert usages[1]["totals"]["tokens"] > usages[0]["totals"]["tokens"]


def test_done_carries_the_result_dict():
    events, _, _ = _go(_final([CSS]))
    assert events[-1]["result"]["suspected_files"] == [CSS]


def test_turn_limit_is_a_budget_stop():
    events, _, err = _go(_tool_turn(), budget=_budget(max_turns=1))
    assert isinstance(err, loop.BudgetExceeded)
    assert err.limit == "turns" and err.value == 1
    assert "no final answer after 1 turns" in str(err)
    failed = events[-1]
    assert failed["type"] == "failed" and failed["reason"] == "budget"
    assert failed["limit"] == "turns" and failed["value"] == 1


def test_token_limit_stops_before_the_next_call():
    _, _, err = _go(_tool_turn(), budget=_budget(max_tokens=15))
    assert err.limit == "tokens" and len(err.run.turns) == 1


def test_dollar_limit_stops_before_the_next_call():
    _, _, err = _go(_tool_turn(), budget=_budget(max_dollars=1e-9))
    assert err.limit == "dollars" and len(err.run.turns) == 1


def test_seconds_limit_stops_before_the_next_call(monkeypatch):
    ticks = iter([0.0])  # the run start; every later read is 200 s on
    monkeypatch.setattr(loop, "_now", lambda: next(ticks, 200.0))
    _, _, err = _go(_tool_turn(), budget=_budget(max_seconds=180))
    assert err.limit == "seconds" and len(err.run.turns) == 1


def test_run_error_emits_failed_with_reason_run():
    bad = _resp([NS(type="text", text="not json")], "end_turn")
    events, _, err = _go(bad)
    assert not isinstance(err, loop.BudgetExceeded)
    assert events[-1]["type"] == "failed" and events[-1]["reason"] == "run"


def test_api_error_emits_failed_with_reason_api():
    class Boom(FakeClient):
        def create(self, **kw):
            raise _ApiError()
    events = []
    with pytest.raises(loop.RunError) as ei:
        loop.run("t", "b", [_hit()], CUTOFF, client=Boom(), on_event=events.append)
    assert str(ei.value).startswith("API error")
    assert events[-1]["reason"] == "api"


def test_raising_callback_does_not_break_the_run():
    def boom(_e):
        raise RuntimeError("x")
    client = FakeClient(_tool_turn(), _final([CSS]))
    out = loop.run("t", "b", [_hit()], CUTOFF, client=client, on_event=boom)
    assert out.result.suspected_files == [CSS]


def test_no_callback_behaves_as_before():
    a = loop.run("t", "b", [_hit()], CUTOFF, client=FakeClient(_tool_turn(), _final([CSS])))
    b = loop.run("t", "b", [_hit()], CUTOFF, client=FakeClient(_tool_turn(), _final([CSS])),
                 on_event=lambda e: None)
    assert a.result == b.result and len(a.turns) == len(b.turns)
    assert [(c.name, c.output) for c in a.trace] == [(c.name, c.output) for c in b.trace]


def test_budget_from_config_reads_the_env(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_DOLLARS", "0.2")
    monkeypatch.setenv("AGENT_MAX_TOKENS", "1234")
    b = loop.Budget.from_config()
    assert b.max_dollars == 0.2 and b.max_tokens == 1234
