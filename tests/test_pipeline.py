"""The staged pipeline, driven by a scripted FakeClient. No API call anywhere."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from agent import pipeline, stages, tools
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
