"""The ablation runner: one record per (arm, issue, repeat), resumable, never raises."""
from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace as NS

import pytest

from agent.loop import Run, RunError, Turn
from eval import ablation
from eval.arms import ARMS

FIELDS = {
    "arm", "repeat", "issue", "mentions_gold_path", "gold", "candidates", "picked",
    "recall_at_1", "recall_at_5", "rr", "turns", "tool_calls", "input_tokens",
    "output_tokens", "cost_usd", "latency_ms", "error", "split", "error_kind",
}
ROW = {"issue": 7, "title": "t", "body": "b", "created_at": "2024-01-02T00:00:00Z",
       "gold_files": ["b"], "mentions_gold_path": False}


def _turn():
    return Turn(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
                cache_read_input_tokens=0, stop_reason="end_turn", request_id=None,
                latency_ms=1, cost_usd=Decimal("0.01"))


def _run(picked, turns=1):
    tc = NS(turn=1, name="grep", input={"pattern": "x"}, output="out", is_error=False,
            duration_ms=1)
    return Run(result=NS(suspected_files=picked), turns=[_turn() for _ in range(turns)],
               trace=[tc])


def _fake(picked):
    def fn(title, body, candidates, cutoff, **kw):
        return _run(picked)
    return fn


def _rec(arm="agent", row=ROW, run_fn=None, cands=("a", "b")):
    return ablation.make_record(ARMS[arm], row, 0, list(cands), "val",
                                run_fn=run_fn or _fake(["a", "b"]))


def test_record_has_every_spec_field():
    r = _rec()
    assert set(r) == FIELDS
    assert r["error"] is None and r["error_kind"] is None
    assert r["turns"] == 1 and isinstance(r["cost_usd"], float) and r["cost_usd"] > 0
    assert r["tool_calls"] == [{"name": "grep", "input": {"pattern": "x"},
                                "is_error": False, "output_preview": "out"}]
    json.dumps(r)


def test_success_scores_with_score_ranking():
    r = _rec()
    assert (r["recall_at_1"], r["recall_at_5"], r["rr"]) == (0, 1, 0.5)


def test_run_error_scores_zero_and_keeps_paid_turns():
    def fn(*a, **k):
        raise RunError("bad", _run([], turns=2))
    r = _rec(run_fn=fn)
    assert r["error_kind"] == "run" and r["error"] == "bad"
    assert r["turns"] == 2 and r["cost_usd"] > 0 and r["picked"] == []
    assert r["recall_at_1"] == 0 and r["rr"] == 0


def test_api_error_is_marked_api():
    def api(*a, **k):
        raise RunError("API error on turn 2: 529", _run([], turns=1))
    def boom(*a, **k):
        raise RuntimeError("x")
    assert _rec(run_fn=api)["error_kind"] == "api"
    r = _rec(run_fn=boom)
    assert r["error_kind"] == "api" and r["turns"] == 0 and r["cost_usd"] == 0


def test_retrieval_arm_needs_no_model():
    def never(*a, **k):
        raise AssertionError("called")
    r = _rec("retrieval", run_fn=never)
    assert r["cost_usd"] == 0 and r["picked"] == ["a", "b"] and r["rr"] == 0.5
    assert r["turns"] == 0 and r["error"] is None


def test_cutoff_is_the_rows_created_at():
    seen = {}
    def fn(title, body, candidates, cutoff, **kw):
        seen["cutoff"], seen["kw"] = cutoff, kw
        return _run(["b"])
    _rec(run_fn=fn)
    assert seen["cutoff"] == ROW["created_at"]
    assert seen["kw"] == {"system": ARMS["agent"].system, "tool_specs": ARMS["agent"].tool_specs}


def test_load_skips_a_partial_last_line(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text('{"arm": "a", "issue": 1, "repeat": 0}\n\n'
                '{"arm": "b", "issue": 2, "repeat": 0}\n{"arm": "ag')
    assert [r["arm"] for r in ablation.load_records(p)] == ["a", "b"]
    assert ablation.load_records(tmp_path / "missing.jsonl") == []


def test_resume_skips_done_but_retries_api_errors():
    recs = [
        {"arm": "agent", "issue": 1, "repeat": 0, "error_kind": None},
        {"arm": "agent", "issue": 2, "repeat": 0, "error_kind": "run"},
        {"arm": "agent", "issue": 3, "repeat": 0, "error_kind": "api"},
    ]
    assert ablation.done_keys(recs) == {("agent", 1, 0), ("agent", 2, 0)}


def test_jobs_are_appended_one_line_each(tmp_path):
    p = tmp_path / "out" / "r.jsonl"
    jobs = [ablation.Job(ARMS["agent"], {**ROW, "issue": i}, rep, "val", lambda: ["a", "b"])
            for i in range(3) for rep in range(2)]
    out = ablation.run_jobs(jobs, p, run_fn=_fake(["b"]))
    lines = p.read_text().splitlines()
    assert len(lines) == 6 and all(set(json.loads(l)) == FIELDS for l in lines)
    assert out["ran"] == 6 and out["spent"] == pytest.approx(0.06)


def test_pending_jobs_skip_done_and_retrieve_only_when_run(monkeypatch):
    calls = []
    monkeypatch.setattr(ablation.search, "search_ranked",
                        lambda q, **k: calls.append(q) or [NS(path="a")])
    rows = [{**ROW, "issue": 1}, {**ROW, "issue": 2}]
    jobs = ablation.pending_jobs([ARMS["agent"]], rows, 2, "val", {("agent", 1, 0), ("agent", 1, 1)})
    assert [(j.row["issue"], j.repeat) for j in jobs] == [(2, 0), (2, 1)]
    assert calls == []
    assert jobs[0].candidates() == jobs[1].candidates() and len(calls) == 1


def test_refuses_a_file_from_another_split(tmp_path, monkeypatch):
    monkeypatch.setattr(ablation, "RUNS_DIR", tmp_path)
    (tmp_path / "x.jsonl").write_text(json.dumps({"arm": "agent", "split": "train"}) + "\n")
    with pytest.raises(SystemExit):
        ablation.main(["--arms", "agent", "--split", "val", "--name", "x"])


def test_refuses_the_test_split(tmp_path, monkeypatch):
    monkeypatch.setattr(ablation, "RUNS_DIR", tmp_path)
    with pytest.raises(SystemExit):
        ablation.main(["--arms", "agent", "--split", "test"])


def test_retrieval_failure_becomes_an_api_record_and_other_jobs_run(tmp_path):
    def bad():
        raise ConnectionError("db down")
    ok = lambda: ["a", "b"]
    jobs = [ablation.Job(ARMS["agent"], {**ROW, "issue": 1}, 0, "val", bad),
            ablation.Job(ARMS["retrieval"], {**ROW, "issue": 2}, 0, "val", bad),
            ablation.Job(ARMS["agent"], {**ROW, "issue": 3}, 0, "val", ok)]
    p = tmp_path / "r.jsonl"
    out = ablation.run_jobs(jobs, p, run_fn=_fake(["b"]))
    recs = {r["issue"]: r for r in ablation.load_records(p)}
    assert out["ran"] == 3
    for i in (1, 2):
        r = recs[i]
        assert set(r) == FIELDS and r["error_kind"] == "api" and "db down" in r["error"]
        assert r["turns"] == 0 and r["cost_usd"] == 0 and r["picked"] == [] and r["candidates"] == []
    assert recs[3]["error"] is None


def test_append_after_a_torn_last_line_keeps_the_new_record(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text('{"arm": "agent", "issue": 1, "repeat": 0}\n{"arm": "ag')
    job = ablation.Job(ARMS["agent"], ROW, 0, "val", lambda: ["a", "b"])
    ablation.run_jobs([job], p, run_fn=_fake(["b"]))
    recs = ablation.load_records(p)
    assert len(recs) == 2 and recs[1]["issue"] == ROW["issue"]


def test_load_skips_parsed_lines_that_are_not_records(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text('{"arm": "a", "issue": 1, "repeat": 0}\n12\n[1]\n{"arm": "x"}\n"s"\n')
    recs = ablation.load_records(p)
    assert len(recs) == 1
    assert ablation.done_keys(recs) == {("a", 1, 0)}


def _jobs(arm, n):
    return [ablation.Job(ARMS[arm], {**ROW, "issue": i}, 0, "val", lambda: ["a", "b"])
            for i in range(n)]


def test_estimate_uses_prior_cost_without_records():
    assert ablation.estimate_cost(_jobs("agent", 10), []) == pytest.approx(10 * 0.0029)


def test_estimate_prefers_measured_cost():
    recs = [{"arm": "agent", "issue": i, "repeat": 0, "cost_usd": 0.004, "error_kind": None}
            for i in range(2)]
    recs.append({"arm": "agent", "issue": 9, "repeat": 0, "cost_usd": 0.5, "error_kind": "api"})
    assert ablation.estimate_cost(_jobs("agent", 10), recs) == pytest.approx(10 * 0.004)


def test_cli_refuses_when_estimate_exceeds_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(ablation, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(ablation.C, "PROVIDER", "anthropic")
    monkeypatch.setattr(ablation, "val_rows",
                        lambda split, n: [{**ROW, "issue": i} for i in range(n)])

    def boom(*a, **k):
        raise AssertionError("must refuse before running")
    monkeypatch.setattr(ablation, "run_jobs", boom)
    monkeypatch.setattr(ablation.search, "search_ranked", boom)
    with pytest.raises(SystemExit):
        ablation.main(["--arms", "agent", "--n", "10", "--name", "x", "--max-dollars", "0.001"])
    assert list(tmp_path.iterdir()) == []


def test_run_stops_once_spend_passes_cap(tmp_path):
    out = ablation.run_jobs(_jobs("agent", 10), tmp_path / "r.jsonl", workers=1,
                            cap_dollars=0.025, run_fn=_fake(["b"]))
    assert out["ran"] == 3 and out["stopped_for_cap"] is True
    assert out["spent"] == pytest.approx(0.03)
    assert len(ablation.load_records(tmp_path / "r.jsonl")) == 3


def test_no_cap_means_not_stopped(tmp_path):
    out = ablation.run_jobs(_jobs("agent", 2), tmp_path / "r.jsonl", workers=1,
                            run_fn=_fake(["b"]))
    assert out["ran"] == 2 and out["stopped_for_cap"] is False
