"""POST /runs and its SSE stream, with search, the loop and the db faked."""
from __future__ import annotations

import json
import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from agent import loop
from api import config as C
from api import live
from api import schema as S
from api.main import app, format_sse
from retrieval.search import FileHit, Hit

BODY = {"title": "Less @import fails", "body": "details"}
TOTALS = {"turns": 1, "tokens": 5, "cost_usd": 0.001, "seconds": 0.1}


def _hit(path):
    return FileHit(path, 0.8, Hit(path, 0.8, 1, 10, "code"))


def _turn():
    return loop.Turn(input_tokens=100, output_tokens=10, cache_creation_input_tokens=0,
                     cache_read_input_tokens=0, stop_reason="end_turn", request_id="req_1",
                     latency_ms=50, cost_usd=Decimal("0.001"))


def _result():
    return S.GroundedTriageResult(
        reasoning="r", issue_type="bug", severity="medium", suspected_components=[],
        reproduction_steps=[], confidence=0.5, suspected_files=["a.ts"])


def _script(preview="ok"):
    return [
        {"type": "step_started", "turn": 1},
        {"type": "usage", "turn": 1, "input_tokens": 100, "output_tokens": 10,
         "cost_usd": 0.001, "totals": TOTALS},
        {"type": "tool_called", "turn": 1, "tool_use_id": "t1", "name": "read_file", "input": {}},
        {"type": "tool_returned", "turn": 1, "tool_use_id": "t1", "name": "read_file",
         "output_preview": preview, "is_error": False, "duration_ms": 3},
        {"type": "done", "result": _result().model_dump(), "totals": TOTALS},
    ]


@pytest.fixture
def env(monkeypatch):
    calls = []

    async def record(**kw):
        calls.append(kw)
        return len(calls)

    async def noop():
        pass

    monkeypatch.setattr(C, "PROVIDER", "anthropic")
    monkeypatch.setattr(C, "LIVE_KEEPALIVE_SECONDS", 0.2)
    monkeypatch.setattr("api.main.db.connect", noop)
    monkeypatch.setattr("api.main.db.disconnect", noop)
    monkeypatch.setattr("api.main.db.record_call", record)
    monkeypatch.setattr("api.main.search.search_ranked",
                        lambda *a, **k: [_hit("a.ts"), _hit("b.ts")])
    monkeypatch.setattr(live, "registry",
                        live.RunRegistry(ttl_seconds=900, max_concurrent=2))

    def set_run(script=None, *, pause=0.0, exc=None, run=None):
        def fake(title, body, candidates, cutoff, **kw):
            fake.cutoff = cutoff
            for e in (script if script is not None else _script()):
                if pause:
                    time.sleep(pause)
                kw["on_event"](e)
            if exc:
                raise exc
            return run or loop.Run(result=_result(), turns=[_turn()])
        monkeypatch.setattr("api.main.agent_loop.run", fake)
        return fake

    set_run()
    # a context manager keeps one event loop alive, so the server-owned task survives
    with TestClient(app) as client:
        yield client, calls, set_run


def parse(text):
    blocks = [b for b in text.split("\n\n") if b]
    out = []
    for b in blocks:
        if b.startswith(":"):
            out.append(("comment", b))
            continue
        lines = b.split("\n")
        assert len(lines) == 3, b
        assert lines[0].startswith("id: ") and lines[1].startswith("event: ")
        assert lines[2].startswith("data: ")
        out.append((int(lines[0][4:]), lines[1][7:], json.loads(lines[2][6:])))
    return out


def stream(client, run_id, **kw):
    r = client.get(f"/runs/{run_id}/events", **kw)
    assert r.status_code == 200
    return r


def start(client):
    r = client.post("/runs", json=BODY)
    assert r.status_code == 202
    return r.json()["run_id"]


def test_post_returns_202_and_a_run_id(env):
    client, *_ = env
    assert len(start(client)) == 32


def test_post_refuses_non_anthropic_provider(env, monkeypatch):
    client, *_ = env
    monkeypatch.setattr(C, "PROVIDER", "openai")
    assert client.post("/runs", json=BODY).status_code == 400


def test_post_429_when_registry_full(env, monkeypatch):
    client, _, set_run = env
    set_run(pause=0.5)
    start(client), start(client)
    assert client.post("/runs", json=BODY).status_code == 429


def test_post_422_for_malformed_created_at(env):
    client, *_ = env
    assert client.post("/runs", json={**BODY, "created_at": "nope"}).status_code == 422


def test_stream_wire_format(env):
    client, *_ = env
    text = stream(client, start(client)).text
    got = parse(text)
    assert [g[0] for g in got] == list(range(1, 7))
    assert got[0][1] == "run_started" and got[-1][1] == "done"
    assert got[0][2]["candidates"][0] == {"path": "a.ts", "score": 0.8, "start_line": 1, "end_line": 10}
    assert text.endswith("\n\n")


def test_stream_headers(env):
    client, *_ = env
    r = stream(client, start(client))
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["content-type"].startswith("text/event-stream")


def test_stream_ends_after_done(env):
    client, *_ = env
    got = parse(stream(client, start(client)).text)  # returns only at EOF
    assert got[-1][1] == "done"


def test_resume_from_last_event_id(env):
    client, *_ = env
    rid = start(client)
    stream(client, rid)
    got = parse(stream(client, rid, headers={"Last-Event-ID": "3"}).text)
    assert got[0][0] == 4 and got[-1][1] == "done"


def test_after_query_resumes(env):
    client, *_ = env
    rid = start(client)
    stream(client, rid)
    assert parse(stream(client, rid, params={"after": 5}).text)[0][0] == 6


def test_header_wins_over_after_query(env):
    client, *_ = env
    rid = start(client)
    stream(client, rid)
    got = parse(stream(client, rid, headers={"Last-Event-ID": "2"}, params={"after": 5}).text)
    assert got[0][0] == 3


def test_after_beyond_last_seq_of_finished_run_ends_at_once(env):
    client, *_ = env
    rid = start(client)
    stream(client, rid)
    t = time.monotonic()
    assert stream(client, rid, headers={"Last-Event-ID": "6"}).text == ""
    assert time.monotonic() - t < 0.15  # keep-alive is 0.2s: it did not wait


def test_two_streams_get_the_same_events(env):
    client, _, set_run = env
    set_run(pause=0.05)
    rid = start(client)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(2) as ex:
        a, b = [ex.submit(lambda: stream(client, rid).text) for _ in range(2)]
        ta, tb = a.result(timeout=10), b.result(timeout=10)
    assert ta == tb and len(parse(ta)) == 6


def test_newlines_in_tool_output_do_not_break_framing(env):
    client, _, set_run = env
    nasty = "a\n\ndata: x\r\nid: 99"
    set_run(_script(preview=nasty))
    got = parse(stream(client, start(client)).text)
    assert [g[0] for g in got] == list(range(1, 7))
    assert got[4][2]["output_preview"] == nasty


def test_format_sse_is_one_data_line():
    out = format_sse({"seq": 2, "type": "usage", "x": "a\n\nb"})
    assert out.count("\n") == 4 and out.startswith("id: 2\nevent: usage\ndata: ")


def test_keep_alive_comment_while_waiting(env):
    client, _, set_run = env
    set_run(_script()[:1] + _script()[-1:], pause=0.5)
    text = stream(client, start(client)).text
    assert ": keep-alive\n\n" in text
    assert parse(text)[-1][1] == "done"


def test_unknown_run_is_404(env):
    client, *_ = env
    assert client.get("/runs/nope/events").status_code == 404
    assert client.get("/runs/nope").status_code == 404


def test_snapshot_reports_status_and_result(env):
    client, *_ = env
    rid = start(client)
    stream(client, rid)
    snap = client.get(f"/runs/{rid}").json()
    assert snap["status"] == "done" and snap["last_seq"] == 6
    assert snap["result"]["suspected_files"] == ["a.ts"] and snap["failure"] is None


def test_limits_route_returns_the_budget(env):
    client, *_ = env
    r = client.get("/runs/limits")
    assert r.status_code == 200
    assert r.json() == {"max_turns": loop.Budget.from_config().max_turns,
                        "max_tokens": loop.Budget.from_config().max_tokens,
                        "max_seconds": loop.Budget.from_config().max_seconds,
                        "max_dollars": loop.Budget.from_config().max_dollars}


def test_calls_rows_recorded_per_turn_under_the_run_id(env):
    client, calls, set_run = env
    set_run(run=loop.Run(result=_result(), turns=[_turn(), _turn()]))
    rid = start(client)
    stream(client, rid)
    deadline = time.monotonic() + 5
    while len(calls) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert [(c["endpoint"], c["run_id"], c["turn"]) for c in calls] == [("/runs", rid, 1), ("/runs", rid, 2)]


def test_cutoff_defaults_to_pin_date(env, monkeypatch):
    client, _, set_run = env
    monkeypatch.setattr("api.main.agent_tools.pinned_date", lambda: "2025-01-01")
    fake = set_run()
    stream(client, start(client))
    assert fake.cutoff == "2025-01-01"


def test_retrieval_failure_emits_failed_api_and_records_one_row(env, monkeypatch):
    client, calls, _ = env

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr("api.main.search.search_ranked", boom)
    rid = start(client)
    got = parse(stream(client, rid).text)
    assert [g[1] for g in got] == ["failed"]
    assert got[0][2]["reason"] == "api" and "db down" in got[0][2]["message"]
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(calls) == 1 and calls[0]["error"] and calls[0]["run_id"] == rid


def test_loop_returning_without_terminal_event_still_ends_failed(env):
    client, _, set_run = env
    set_run(_script()[:2])  # callback never stores done
    rid = start(client)
    got = parse(stream(client, rid).text)
    assert got[-1][1] == "failed" and got[-1][2]["reason"] == "api"
    assert [g[1] for g in got].count("failed") == 1
    assert client.get(f"/runs/{rid}").json()["status"] == "failed"


def test_rejected_done_event_still_ends_failed(env):
    client, _, set_run = env
    set_run(_script()[:-1] + [{"type": "done", "result": {"bad": 1}, "totals": TOTALS}])
    got = parse(stream(client, start(client)).text)
    assert got[-1][1] == "failed"


def test_loop_crash_ends_failed_and_budget_keeps_limit(env):
    client, _, set_run = env
    set_run(_script()[:2], exc=ValueError("kaboom"))
    got = parse(stream(client, start(client)).text)
    assert got[-1][1] == "failed" and "kaboom" in got[-1][2]["message"]
    r = loop.Run(turns=[_turn()])
    set_run(_script()[:2], exc=loop.BudgetExceeded("dollars budget", r, limit="dollars", value=1.0))
    got = parse(stream(client, start(client)).text)
    assert got[-1][2]["reason"] == "budget" and got[-1][2]["limit"] == "dollars"
