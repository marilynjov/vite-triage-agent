"""The run registry: numbered events, waiting streams, TTL, concurrency limit."""
from __future__ import annotations

import threading
import time

import pytest
from pydantic import ValidationError

from api.live import RegistryFull, RunRegistry
from tests.test_live_schema import TOTALS

STEP = {"type": "step_started", "turn": 1}
FAILED = {"type": "failed", "reason": "api", "message": "boom", "totals": TOTALS}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(ttl=900, cap=2):
    clock = Clock()
    return RunRegistry(ttl_seconds=ttl, max_concurrent=cap, clock=clock), clock


def test_seq_numbers_from_one():
    reg, _ = make()
    rid = reg.start()
    assert reg.append(rid, dict(STEP)) == 1
    assert reg.append(rid, dict(STEP)) == 2


def test_events_after():
    reg, _ = make()
    rid = reg.start()
    for _ in range(3):
        reg.append(rid, dict(STEP))
    assert [e["seq"] for e in reg.events_after(rid, 0)] == [1, 2, 3]
    assert [e["seq"] for e in reg.events_after(rid, 2)] == [3]
    assert reg.events_after(rid, 3) == []
    assert reg.events_after("nope", 0) == []


def test_wait_after_wakes_on_append():
    reg, _ = make()
    rid = reg.start()
    out = []
    t = threading.Thread(target=lambda: out.append(reg.wait_after(rid, 0, 2.0)))
    t.start()
    reg.append(rid, dict(STEP))
    t.join(3)
    assert not t.is_alive()
    assert [e["seq"] for e in out[0]] == [1]


def test_wait_after_times_out_empty():
    reg, _ = make()
    rid = reg.start()
    assert reg.wait_after(rid, 0, 0.05) == []


def test_two_waiters_both_receive():
    reg, _ = make()
    rid = reg.start()
    out = []
    ts = [threading.Thread(target=lambda: out.append(reg.wait_after(rid, 0, 2.0))) for _ in range(2)]
    for t in ts:
        t.start()
    reg.append(rid, dict(STEP))
    for t in ts:
        t.join(3)
        assert not t.is_alive()
    assert len(out) == 2 and all([e["seq"] for e in o] == [1] for o in out)


def test_wait_after_on_finished_run_returns_immediately():
    reg, _ = make()
    rid = reg.start()
    reg.append(rid, dict(FAILED))
    t0 = time.monotonic()
    assert reg.wait_after(rid, 1, 5.0) == []
    assert time.monotonic() - t0 < 0.5
    t0 = time.monotonic()
    assert len(reg.wait_after(rid, 0, 5.0)) == 1
    assert time.monotonic() - t0 < 0.5
    t0 = time.monotonic()
    assert reg.wait_after("nope", 0, 5.0) == []
    assert time.monotonic() - t0 < 0.5


def test_done_and_failed_set_status_and_finished_at():
    reg, clock = make()
    rid = reg.start()
    assert reg.get(rid).status == "running" and reg.get(rid).finished_at is None
    reg.append(rid, dict(STEP))
    assert reg.get(rid).status == "running"
    reg.append(rid, dict(FAILED))
    run = reg.get(rid)
    assert run.status == "failed" and run.finished_at == clock.now
    stored = reg.events_after(rid, 1)[0]
    assert stored["limit"] is None and stored["value"] is None
    from tests.test_loop import CSS
    from tests.test_loop_events import _final, _go, _tool_turn

    events, _, _ = _go(_tool_turn(), _final([CSS]))
    rid2 = reg.start()
    for e in events:
        reg.append(rid2, e)
    assert reg.get(rid2).status == "done"
    assert isinstance(reg.events_after(rid2, 0)[-1]["result"], dict)


def test_finished_runs_expire_after_ttl():
    reg, clock = make(ttl=900)
    rid = reg.start()
    reg.append(rid, dict(FAILED))
    clock.now += 899
    assert reg.get(rid) is not None
    clock.now += 2
    assert reg.get(rid) is None
    assert reg.events_after(rid, 0) == [] and reg.wait_after(rid, 0, 0.01) == []


def test_running_runs_never_expire():
    reg, clock = make(ttl=900)
    rid = reg.start()
    clock.now += 100000
    assert reg.get(rid) is not None


def test_concurrency_limit():
    reg, _ = make(cap=2)
    a = reg.start()
    reg.start()
    with pytest.raises(RegistryFull):
        reg.start()
    reg.append(a, dict(FAILED))
    reg.start()


def test_invalid_event_is_rejected():
    reg, _ = make()
    rid = reg.start()
    with pytest.raises(ValidationError):
        reg.append(rid, {"type": "nope"})
    assert reg.get(rid).events == []
    assert reg.append(rid, dict(STEP)) == 1


def test_returned_data_is_a_copy():
    reg, _ = make()
    rid = reg.start()
    reg.append(rid, dict(FAILED))
    ev = reg.events_after(rid, 0)[0]
    ev["seq"] = 99
    ev["totals"]["turns"] = 99
    ev2 = reg.wait_after(rid, 0, 0.05)[0]
    ev2["message"] = "changed"
    run = reg.get(rid)
    run.events.clear()
    run.status = "running"
    stored = reg.events_after(rid, 0)[0]
    assert stored["seq"] == 1 and stored["totals"]["turns"] == 1
    assert stored["message"] == "boom"
    assert reg.get(rid).status == "failed" and len(reg.get(rid).events) == 1
