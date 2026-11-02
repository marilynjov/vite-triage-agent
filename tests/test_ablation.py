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


def _file_with_spend(tmp_path, dollars, name="x"):
    rec = {"arm": "grounded", "issue": 999, "repeat": 0, "cost_usd": dollars,
           "error_kind": "run", "split": "val"}
    (tmp_path / f"{name}.jsonl").write_text(json.dumps(rec) + "\n")


def _cli_env(tmp_path, monkeypatch):
    monkeypatch.setattr(ablation, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(ablation.C, "PROVIDER", "anthropic")
    monkeypatch.setattr(ablation, "val_rows",
                        lambda split, n: [{**ROW, "issue": i} for i in range(n)])


def test_cli_cap_bounds_the_files_lifetime_spend(tmp_path, monkeypatch, capsys):
    _cli_env(tmp_path, monkeypatch)
    _file_with_spend(tmp_path, 2.90)

    def boom(*a, **k):
        raise AssertionError("must refuse before running")
    monkeypatch.setattr(ablation, "run_jobs", boom)
    # 70 agent jobs at the 0.0029 prior is about $0.20: fine alone, over the cap with $2.90 spent
    with pytest.raises(SystemExit):
        ablation.main(["--arms", "agent", "--n", "70", "--repeats", "1", "--name", "x",
                       "--max-dollars", "3"])
    assert "already spent in this file" in capsys.readouterr().out


def test_cli_passes_remaining_budget_to_run_jobs(tmp_path, monkeypatch, capsys):
    _cli_env(tmp_path, monkeypatch)
    _file_with_spend(tmp_path, 2.98)
    seen = {}

    def fake_run_jobs(jobs, path, *, cap_dollars=None, **k):
        seen["cap"] = cap_dollars
        return {"ran": 0, "spent": 0.0, "stopped_for_cap": False}
    monkeypatch.setattr(ablation, "run_jobs", fake_run_jobs)
    ablation.main(["--arms", "agent", "--n", "1", "--repeats", "1", "--name", "x",
                   "--max-dollars", "3"])
    assert seen["cap"] == pytest.approx(0.02)
    assert "cap $3.00 for this file" in capsys.readouterr().out


def test_resume_stops_once_file_total_passes_cap(tmp_path):
    # file already holds $2.98; remaining budget 0.02; each run costs 0.01
    out = ablation.run_jobs(_jobs("agent", 10), tmp_path / "r.jsonl", workers=1,
                            cap_dollars=3.0 - 2.98, run_fn=_fake(["b"]))
    assert out["stopped_for_cap"] is True
    assert 2.98 + out["spent"] > 3.0 and 2.98 + out["spent"] <= 3.0 + 0.011


# --- the staged arm ---------------------------------------------------------

from agent.pipeline import PipelineError, PipelineRun  # noqa: E402
from api.schema import LocateResult, LocatedFile, Region, UnderstandResult  # noqa: E402

STAGED_KEYS = FIELDS | {"stages", "stage_costs", "failed_stage", "outcome", "fault_hit",
                        "file_level_only", "dropped"}
REGIONS = {7: {"b": [[10, 20]]}}


class _Hyp:
    def __init__(self, files, fault):
        self.suspected_files, self._fault = files, fault

    def model_dump(self, **kw):
        return {"suspected_files": self.suspected_files, "fault": self._fault}


def _pipeline_run(files=("b",), hyp=True):
    pr = PipelineRun(
        locate=LocateResult(reasoning="r", files=[LocatedFile(path="b", why="w", evidence="e")]),
        understand=UnderstandResult(reasoning="r", regions=[
            Region(path="b", symbol=None, start_line=12, end_line=15, what_it_does="x",
                   relevance="y")]),
        runs={"locate": _run(["b"]), "understand": _run(["b"], turns=2)})
    if hyp:
        pr.hypothesis = _Hyp(list(files), {"path": "b", "start_line": 11, "end_line": 13,
                                           "mechanism": "m"})
        pr.runs["hypothesize"] = _run(["b"])
    return pr


def _staged(pipeline_fn, regions=REGIONS, row=ROW):
    return ablation.make_record(ARMS["staged"], row, 0, ["a", "b"], "val",
                                pipeline_fn=pipeline_fn, regions=regions)


def test_staged_record_has_stage_fields_and_outcome():
    calls = []

    def fn(title, body, candidates, cutoff, **kw):
        calls.append(kw)
        return _pipeline_run()
    r = _staged(fn)
    assert calls == [{}]  # pipeline_fn really ran, with no system/tool_specs
    assert set(r) == STAGED_KEYS
    assert r["picked"] == ["b"] and r["error"] is None and r["error_kind"] is None
    assert r["outcome"] == "hit" and r["fault_hit"] is True and r["failed_stage"] is None
    assert r["file_level_only"] is False
    assert set(r["stage_costs"]) == {"locate", "understand", "hypothesize"}
    assert r["turns"] == 4 and r["cost_usd"] > 0
    json.dumps(r)


def test_staged_missing_regions_are_file_level():
    r = _staged(lambda *a, **k: _pipeline_run(), regions={})
    assert r["outcome"] == "hit" and r["fault_hit"] is None and r["file_level_only"] is True


def test_staged_pipeline_error_records_failed_stage():
    pr = _pipeline_run(hyp=False)

    def fn(*a, **k):
        raise PipelineError("turn cap", pr, "hypothesize")
    r = _staged(fn)
    assert r["outcome"] == "error:hypothesize" and r["failed_stage"] == "hypothesize"
    assert r["error_kind"] == "run" and r["fault_hit"] is None
    assert (r["recall_at_1"], r["recall_at_5"], r["rr"]) == (0, 0, 0)
    assert r["cost_usd"] > 0 and r["turns"] == 3 and set(r["stage_costs"]) == {"locate", "understand"}
    assert r["stages"]["locate"] is not None and r["stages"]["hypothesis"] is None


def test_staged_early_error_keeps_locate_cost():
    def fn(*a, **k):
        raise PipelineError("API error: boom", PipelineRun(runs={"locate": _run([])}), "locate")
    r = _staged(fn)
    assert r["error_kind"] == "api" and r["outcome"] is None and r["fault_hit"] is None
    assert r["failed_stage"] == "locate" and r["cost_usd"] > 0
    assert ablation.done_keys([r]) == set()


def test_staged_non_run_error_is_infrastructure():
    def fn(*a, **k):
        raise ConnectionError("down")
    r = _staged(fn)
    assert r["error_kind"] == "api" and r["outcome"] is None and r["failed_stage"] is None
    assert r["stages"] == {"locate": None, "understand": None, "hypothesis": None}
    assert ablation.done_keys([r]) == set()  # resume retries it


def test_other_arms_records_unchanged():
    assert set(_rec("agent")) == FIELDS
    assert set(_rec("retrieval")) == FIELDS


def test_run_jobs_staged_calls_pipeline_fn(tmp_path):
    seen = []

    def fn(*a, **k):
        seen.append(1)
        return _pipeline_run()
    job = ablation.Job(ARMS["staged"], ROW, 0, "val", lambda: ["a", "b"])
    ablation.run_jobs([job], tmp_path / "r.jsonl", workers=1, pipeline_fn=fn, regions=REGIONS)
    rec = json.loads((tmp_path / "r.jsonl").read_text())
    assert seen == [1] and rec["outcome"] == "hit"


def test_retrieval_failure_on_staged_never_runs_the_pipeline(tmp_path):
    def boom():
        raise OSError("index gone")

    def real(*a, **k):
        raise AssertionError("pipeline must not run")
    job = ablation.Job(ARMS["staged"], ROW, 0, "val", boom)
    ablation.run_jobs([job], tmp_path / "r.jsonl", workers=1, pipeline_fn=real, regions={})
    rec = json.loads((tmp_path / "r.jsonl").read_text())
    assert rec["error_kind"] == "api" and "index gone" in rec["error"] and rec["cost_usd"] == 0


def test_staged_prior_cost():
    job = ablation.Job(ARMS["staged"], ROW, 0, "val", lambda: [])
    assert ablation.estimate_cost([job], []) == pytest.approx(0.010)


def test_staged_wrapped_infrastructure_error_is_api():
    def fn(*a, **k):
        try:
            raise ConnectionError("down")
        except ConnectionError as c:
            raise PipelineError("ConnectionError: down", _pipeline_run(hyp=False), "locate") from c
    r = _staged(fn)
    assert r["error_kind"] == "api" and r["outcome"] is None and r["failed_stage"] is None
    assert r["cost_usd"] > 0
    assert ablation.done_keys([r]) == set()


def test_staged_run_error_cause_stays_run():
    def fn(*a, **k):
        try:
            raise RunError("cap", _pipeline_run())
        except RunError as c:
            raise PipelineError("cap", _pipeline_run(hyp=False), "understand") from c
    r = _staged(fn)
    assert r["error_kind"] == "run" and r["outcome"] == "error:understand"


def test_staged_without_regions_raises(tmp_path):
    with pytest.raises(ValueError):
        ablation.make_record(ARMS["staged"], ROW, 0, [], "val", pipeline_fn=lambda *a, **k: 1)
    job = ablation.Job(ARMS["staged"], ROW, 0, "val", lambda: [])
    with pytest.raises(ValueError):
        ablation.run_jobs([job], tmp_path / "r.jsonl")
