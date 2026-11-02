"""The staged pipeline, driven by a scripted FakeClient. No API call anywhere."""
from __future__ import annotations

from types import SimpleNamespace as NS

import anthropic
import pytest

from agent import loop, pipeline, stages, tools
from agent.loop import Budget, RunError
from api import schema as S
from retrieval.search import FileHit, Hit

CSS = "packages/vite/src/node/plugins/css.ts"
CUTOFF = "2024-01-01T00:00:00Z"
BIG = Budget(max_turns=99, max_tokens=10**9, max_seconds=10**6, max_dollars=100.0)


def _hit(path=CSS):
    return FileHit(path, 0.8, Hit(path, 0.8, 1, 40, "code"))


def _resp(model):
    usage = NS(input_tokens=100, output_tokens=10,
               cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return NS(content=[NS(type="text", text=model.model_dump_json())], stop_reason="end_turn",
              usage=usage, model="claude-haiku-5-5", _request_id="req_x")


class FakeClient:
    def __init__(self, *responses):
        self.calls, self._responses = [], list(responses)
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return self._responses.pop(0)


def _loc():
    return S.LocateResult(reasoning="r", files=[S.LocatedFile(path=CSS, why="w", evidence="e")])


def _und(path=CSS):
    return S.UnderstandResult(reasoning="r", regions=[S.Region(
        path=path, symbol=None, start_line=1, end_line=10, what_it_does="d", relevance="r")])


def _hyp():
    return S.HypothesisResult(
        reasoning="r", issue_type="bug", severity="medium", suspected_components=[],
        reproduction_steps=[], confidence=0.5, suspected_files=[CSS],
        fault=S.Fault(path=CSS, start_line=1, end_line=5, mechanism="m"))


def _go(client, **kw):
    kw.setdefault("budget", BIG)
    return pipeline.run_pipeline("t", "b", [_hit()], CUTOFF, client=client, **kw)


def _first_user(call):
    return call["messages"][0]["content"]


def test_stages_run_in_order_with_their_tools():
    c = FakeClient(_resp(_loc()), _resp(_und()), _resp(_hyp()))
    _go(c)
    loc, und, hyp = c.calls
    assert loc["tools"] == tools.TOOL_SPECS
    assert {t["name"] for t in und["tools"]} == {"read_file", "grep"}
    assert "tools" not in hyp


def test_each_stage_sees_the_previous_result():
    c = FakeClient(_resp(_loc()), _resp(_und()), _resp(_hyp()))
    _go(c)
    assert CSS in _first_user(c.calls[1])
    assert _loc().model_dump_json(indent=2) in _first_user(c.calls[2])
    # understand gets path and why only, not the evidence
    assert "- " + CSS + ": w" in _first_user(c.calls[1])
    assert "evidence" not in _first_user(c.calls[1])
    assert _und().model_dump_json(indent=2) in _first_user(c.calls[2])


def test_result_is_the_hypothesis():
    pr = _go(FakeClient(_resp(_loc()), _resp(_und()), _resp(_hyp())))
    assert pr.result.fault.path == CSS and pr.failed_stage is None
    assert set(pr.stage_costs) == {"locate", "understand", "hypothesize"}


def test_budget_carries_across_stages():
    c = FakeClient(_resp(_loc()))
    tiny = Budget(max_turns=99, max_tokens=10**9, max_seconds=10**6, max_dollars=1e-9)
    with pytest.raises(pipeline.PipelineError) as ei:
        _go(c, budget=tiny)
    assert ei.value.stage == "understand" and len(c.calls) == 1
    assert ei.value.run.failed_stage == "understand" and ei.value.run.locate is not None
    assert ei.value.run.cost_usd > 0


def test_understand_run_error_is_attributed():
    c = FakeClient(_resp(_loc()), _resp(_und("packages/vite/src/node/config.ts")))
    with pytest.raises(pipeline.PipelineError) as ei:
        _go(c)
    e = ei.value
    assert e.stage == "understand" and isinstance(e, RunError)
    assert str(e).startswith("final answer rejected: understand:")
    assert e.run.locate is not None and e.run.understand is None
    # the rejected stage's spend is still counted
    assert len(e.run.turns) == 2 and e.run.stage_costs["understand"] > 0


def test_pipeline_run_duck_types_run():
    pr = _go(FakeClient(_resp(_loc()), _resp(_und()), _resp(_hyp())))
    assert len(pr.turns) == sum(len(r.turns) for r in pr.runs.values()) == 3
    assert pr.cost_usd == sum(r.cost_usd for r in pr.runs.values())
    assert pr.input_tokens == 300 and pr.output_tokens == 30 and pr.trace == []


def _spy(monkeypatch, seconds_per_stage=5.0):
    """Record each stage's budget; make the clock deterministic."""
    clock = [0.0]
    monkeypatch.setattr(loop, "_now", lambda: clock[0])
    real, seen = loop.run, []

    def wrapped(*a, **kw):
        seen.append(kw["budget"])
        try:
            return real(*a, **kw)
        finally:
            clock[0] += seconds_per_stage

    monkeypatch.setattr(loop, "run", wrapped)
    return seen


def test_each_stage_budget_is_the_pipeline_budget_minus_earlier_spend(monkeypatch):
    seen = _spy(monkeypatch)
    b = Budget(max_turns=99, max_tokens=1000, max_seconds=100.0, max_dollars=1.0)
    c = FakeClient(_resp(_loc()), _resp(_und()), _resp(_hyp()))
    pr = _go(c, budget=b)
    locate_cost = float(pr.runs["locate"].cost_usd)
    assert [x.max_turns for x in seen] == [8, 6, 1]
    assert seen[0] == Budget(8, 1000, 100.0, 1.0)
    assert seen[1] == Budget(6, 1000 - 110, 95.0, 1.0 - locate_cost)
    assert seen[2].max_tokens == 1000 - 220 and seen[2].max_seconds == 90.0
    assert seen[2].max_dollars == pytest.approx(1.0 - 2 * locate_cost)


def test_tokens_spent_exactly_stop_the_next_stage_with_no_call(monkeypatch):
    _spy(monkeypatch)
    b = Budget(max_turns=99, max_tokens=110, max_seconds=100.0, max_dollars=100.0)
    c = FakeClient(_resp(_loc()))
    with pytest.raises(pipeline.PipelineError) as ei:
        _go(c, budget=b)
    assert ei.value.stage == "understand" and len(c.calls) == 1
    assert ei.value.limit == "tokens" and ei.value.value == 0


def test_pre_start_dollar_stop_carries_limit():
    with pytest.raises(pipeline.PipelineError) as ei:
        _go(FakeClient(_resp(_loc())), budget=Budget(99, 10**9, 10**6, 1e-9))
    assert ei.value.limit == "dollars" and ei.value.value < 0


def test_turn_cap_mid_stage_is_attributed_with_limit():
    def tool():
        return NS(content=[NS(type="tool_use", id="t", name="read_file",
                              input={"path": "nope.ts"})],
                  stop_reason="tool_use", usage=NS(
                      input_tokens=100, output_tokens=10, cache_creation_input_tokens=0,
                      cache_read_input_tokens=0), model="m", _request_id="r")
    c = FakeClient(_resp(_loc()), *[tool() for _ in range(6)])
    with pytest.raises(pipeline.PipelineError) as ei:
        _go(c)
    e = ei.value
    assert e.stage == "understand" and e.limit == "turns" and e.value == 6
    assert len(e.run.runs["understand"].turns) == 6 and e.run.locate is not None


class _ApiError(anthropic.APIError):
    def __init__(self, message):
        Exception.__init__(self, message)
        self.message = message


class _Boom(FakeClient):
    def __init__(self, *responses, exc):
        super().__init__(*responses)
        self.exc = exc

    def create(self, **kw):
        if not self._responses:
            raise self.exc
        return super().create(**kw)


def test_api_error_in_a_stage_keeps_its_prefix():
    c = _Boom(_resp(_loc()), exc=_ApiError("down"))
    with pytest.raises(pipeline.PipelineError) as ei:
        _go(c)
    assert str(ei.value).startswith("API error") and ei.value.stage == "understand"
    assert ei.value.limit is None and ei.value.run.locate is not None


def test_unexpected_exception_is_attributed_and_keeps_earlier_spend():
    c = _Boom(_resp(_loc()), exc=KeyError("kaboom"))
    with pytest.raises(pipeline.PipelineError) as ei:
        _go(c)
    e = ei.value
    assert e.stage == "understand" and str(e) == "KeyError: 'kaboom'"
    assert e.run.failed_stage == "understand" and set(e.run.runs) == {"locate"}
    assert e.run.cost_usd > 0
