"""POST /triage/agent: the route's accounting, with search, db and the loop faked."""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from agent import loop, tools
from api import config as C
from api import schema as S
from api.main import app
from retrieval.search import FileHit, Hit

BODY = {"title": "Less @import fails", "body": "details"}


def _hit(path):
    return FileHit(path, 0.8, Hit(path, 0.8, 1, 10, "code"))


def _turn(n):
    return loop.Turn(
        input_tokens=100 * n, output_tokens=10 * n, cache_creation_input_tokens=0,
        cache_read_input_tokens=0, stop_reason="tool_use" if n < 3 else "end_turn",
        request_id=f"req_{n}", latency_ms=50 * n, cost_usd=Decimal("0.001"),
    )


def _result():
    return S.GroundedTriageResult(
        reasoning="r", issue_type="bug", severity="medium", suspected_components=[],
        reproduction_steps=[], confidence=0.5, suspected_files=["a.ts"],
    )


@pytest.fixture
def env(monkeypatch):
    calls, seen = [], {}

    async def record(**kw):
        calls.append(kw)
        return len(calls)

    monkeypatch.setattr(C, "PROVIDER", "anthropic")
    monkeypatch.setattr("api.main.search.search_ranked", lambda *a, **k: [_hit("a.ts"), _hit("b.ts")])
    monkeypatch.setattr("api.main.db.record_call", record)

    def set_run(run=None, exc=None):
        def fake(title, body, candidates, cutoff, **kw):
            seen["cutoff"] = cutoff
            if exc:
                raise exc
            return run
        monkeypatch.setattr("api.main.agent_loop.run", fake)

    return calls, seen, set_run


def test_refuses_when_provider_is_not_anthropic(monkeypatch, env):
    monkeypatch.setattr(C, "PROVIDER", "ollama")
    assert TestClient(app).post("/triage/agent", json=BODY).status_code == 400


def test_success_records_one_row_per_turn_with_a_shared_run_id(env):
    calls, _, set_run = env
    set_run(loop.Run(result=_result(), turns=[_turn(1), _turn(2), _turn(3)]))
    r = TestClient(app).post("/triage/agent", json=BODY)
    assert r.status_code == 200, r.text
    assert r.json()["turns"] == 3
    assert len(calls) == 3
    assert {c["run_id"] for c in calls} == {r.json()["run_id"]}
    assert [c["turn"] for c in calls] == [1, 2, 3]
    assert {c["endpoint"] for c in calls} == {"/triage/agent"}
    assert calls[1]["request_id"] == "req_2" and calls[1]["latency_ms"] == 100


def test_failure_records_the_paid_turns_then_500(env):
    calls, _, set_run = env
    set_run(exc=loop.RunError("boom", loop.Run(turns=[_turn(1), _turn(2)])))
    r = TestClient(app).post("/triage/agent", json=BODY)
    assert r.status_code == 500
    assert len(calls) == 2
    assert calls[0].get("error") is None and calls[1]["error"] == "boom"


def test_failure_before_any_turn_records_one_error_row(env):
    calls, _, set_run = env
    set_run(exc=loop.RunError("early", loop.Run()))
    assert TestClient(app).post("/triage/agent", json=BODY).status_code == 500
    assert len(calls) == 1 and calls[0]["error"] == "early"


def test_cutoff_defaults_to_the_pin_date(env):
    _, seen, set_run = env
    set_run(loop.Run(result=_result(), turns=[_turn(3)]))
    TestClient(app).post("/triage/agent", json=BODY)
    assert seen["cutoff"] == tools.pinned_date()


def test_non_run_error_records_one_error_row_then_500(env):
    calls, _, set_run = env
    set_run(exc=Exception("kaboom"))
    assert TestClient(app).post("/triage/agent", json=BODY).status_code == 500
    assert len(calls) == 1 and calls[0]["error"] == "kaboom"


def test_malformed_created_at_is_422(env):
    _, seen, set_run = env
    set_run(loop.Run(result=_result(), turns=[_turn(3)]))
    r = TestClient(app).post("/triage/agent", json={**BODY, "created_at": "garbage-not-a-date"})
    assert r.status_code == 422
    assert "cutoff" not in seen


def test_valid_created_at_becomes_the_iso_cutoff(env):
    _, seen, set_run = env
    set_run(loop.Run(result=_result(), turns=[_turn(3)]))
    r = TestClient(app).post("/triage/agent", json={**BODY, "created_at": "2023-07-20T10:52:02Z"})
    assert r.status_code == 200, r.text
    assert seen["cutoff"] == "2023-07-20T10:52:02+00:00"
