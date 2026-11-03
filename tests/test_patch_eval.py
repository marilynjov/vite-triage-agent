"""The patch runner: outcomes from hand-made records and fakes; no model, no Docker."""
from __future__ import annotations

import json
import threading
from decimal import Decimal
from types import SimpleNamespace as NS

import anthropic
import httpx
import pytest

from agent.loop import BudgetExceeded, Run, RunError
from api.schema import Edit, PatchResult
from eval import container, patch_eval
from eval.fixes import Fix
from eval.verify import VerifyResult

FIX = Fix(7, "fixsha", "presha", ("t.spec.ts",), ("t.spec.ts",), (), ())
ROW = {"issue": 7, "title": "t", "body": "b", "created_at": "2024-01-02T00:00:00Z"}
HYP = {"fault": {"path": "packages/vite/src/a.ts", "start_line": 1, "end_line": 2}}


def src(issue=7, repeat=0, **kw):
    rec = {"arm": "staged", "issue": issue, "repeat": repeat, "outcome": "hit",
           "fault_hit": True, "cost_usd": 0.01, "stages": {"hypothesis": HYP}}
    return {**rec, **kw}


def job(**kw):
    return patch_eval.PatchJob(7, 0, ROW, src(), FIX)._replace(**kw)


PATCH = PatchResult(reasoning="r", edits=[Edit(path="p", old="a", new="b")])


def good_run(cost="0.02"):
    return Run(result=PATCH, turns=[NS(cost_usd=Decimal(cost))], trace=[])


class Fakes:
    def __init__(self, run=None, status="pass", exc=None):
        self.patch_calls, self.verify_calls = [], []
        self.run, self.status, self.exc = run or good_run(), status, exc

    def patch_fn(self, *a, **k):
        self.patch_calls.append((a, k))
        if self.exc:
            raise self.exc
        return self.run

    def verify_fn(self, fix, edits):
        self.verify_calls.append((fix, edits))
        return VerifyResult(self.status == "pass", 0, 1.0, "tail", self.status == "timeout",
                            self.status)


@pytest.fixture(autouse=True)
def no_git_no_docker(monkeypatch):
    monkeypatch.setattr(patch_eval, "path_exists", lambda sha, path: True)
    monkeypatch.setattr(patch_eval, "patch_to_diff", lambda edits, rev: "DIFF")
    monkeypatch.setattr(container, "prepare", lambda fix: (True, ""))


def rec(fakes, j=None):
    return patch_eval.make_patch_record(j or job(), patch_fn=fakes.patch_fn,
                                        verify_fn=fakes.verify_fn)


def test_pass_record_has_every_field():
    f = Fakes()
    r = rec(f)
    assert r["outcome"] == "pass" and r["error_kind"] is None
    assert set(r) == {"issue", "repeat", "source_outcome", "source_fault_hit", "prefix_sha",
                      "patch", "diff", "verify", "outcome", "cost_usd", "turns", "error",
                      "error_kind"}
    assert r["source_outcome"] == "hit" and r["source_fault_hit"] is True
    assert r["prefix_sha"] == "presha" and r["diff"] == "DIFF"
    assert r["patch"]["edits"][0]["path"] == "p" and r["verify"]["status"] == "pass"
    assert r["cost_usd"] == pytest.approx(0.02) and r["turns"] == 1
    json.dumps(r)
    (args, kw) = f.patch_calls[0]
    assert args == ("t", "b", HYP, "presha", "2024-01-02T00:00:00Z")


def test_verify_gets_the_records_own_fix():
    f = Fakes()
    rec(f)
    assert f.verify_calls[0][0] is FIX
    assert f.verify_calls[0][1] == PATCH.edits


@pytest.mark.parametrize("status", ["test_fail", "timeout"])
def test_test_fail_and_timeout_are_test_fail(status):
    r = rec(Fakes(status=status))
    assert r["outcome"] == "test_fail" and r["error_kind"] is None
    assert r["verify"]["status"] == status


def test_infra_verify_is_null_outcome_infra_kind():
    r = rec(Fakes(status="infra"))
    assert r["outcome"] is None and r["error_kind"] == "infra"


def test_prepare_failure_is_infra_and_skips_verify(monkeypatch):
    monkeypatch.setattr(container, "prepare", lambda fix: (False, "no space"))
    f = Fakes()
    r = rec(f)
    assert r["outcome"] is None and r["error_kind"] == "infra" and "no space" in r["error"]
    assert not f.verify_calls and r["cost_usd"] == pytest.approx(0.02)


def test_prepare_and_verify_run_under_the_lock(monkeypatch):
    held = []
    monkeypatch.setattr(container, "prepare",
                        lambda fix: (held.append(container.LOCK._is_owned()), (True, ""))[1])
    f = Fakes()
    orig = f.verify_fn
    f.verify_fn = lambda fix, edits: (held.append(container.LOCK._is_owned()),
                                      orig(fix, edits))[1]
    rec(f)
    assert held == [True, True]


def test_stale_path_makes_no_model_call(monkeypatch):
    seen = []
    monkeypatch.setattr(patch_eval, "path_exists",
                        lambda sha, path: seen.append((sha, path)) or False)
    f = Fakes()
    r = rec(f)
    assert r["outcome"] == "stale_path" and r["cost_usd"] == 0.0 and r["turns"] == 0
    assert not f.patch_calls and not f.verify_calls and r["patch"] is None
    assert seen == [("presha", "packages/vite/src/a.ts")]


def test_patch_error_keeps_partial_cost():
    part = Run(result=None, turns=[NS(cost_usd=Decimal("0.03"))], trace=[])
    r = rec(Fakes(exc=RunError("final answer rejected", part)))
    assert r["outcome"] == "patch_error" and r["error_kind"] is None
    assert r["cost_usd"] == pytest.approx(0.03) and r["turns"] == 1 and r["patch"] is None


def test_budget_stop_is_a_patch_error():
    part = Run(result=None, turns=[NS(cost_usd=Decimal("0.05"))], trace=[])
    r = rec(Fakes(exc=BudgetExceeded("over", part, limit="usd", value=0.05)))
    assert r["outcome"] == "patch_error" and r["cost_usd"] == pytest.approx(0.05)


def _api_error():
    return anthropic.APIConnectionError(request=httpx.Request("POST", "http://x"))


def test_api_error_has_null_outcome():
    r = rec(Fakes(exc=_api_error()))
    assert r["outcome"] is None and r["error_kind"] == "api"


def test_run_error_caused_by_api_error_is_api():
    part = Run(result=None, turns=[NS(cost_usd=Decimal("0.01"))], trace=[])
    e = RunError("API error on turn 1: x", part)
    e.__cause__ = _api_error()
    r = rec(Fakes(exc=e))
    assert r["outcome"] is None and r["error_kind"] == "api"
    assert r["cost_usd"] == pytest.approx(0.01)


def test_other_exception_is_unexpected_and_not_retried():
    r = rec(Fakes(exc=ValueError("boom")))
    assert r["outcome"] is None and r["error_kind"] == "unexpected" and "boom" in r["error"]
    assert patch_eval.done_keys([r]) == {(7, 0)}


def test_exception_carrying_a_run_keeps_its_cost():
    e = ValueError("boom")
    e.run = Run(result=None, turns=[NS(cost_usd=Decimal("0.04"))], trace=[])
    r = rec(Fakes(exc=e))
    assert r["error_kind"] == "unexpected" and r["cost_usd"] == pytest.approx(0.04)


def test_failure_after_the_paid_call_keeps_cost_and_is_unexpected(monkeypatch):
    def bad(edits, rev):
        raise RuntimeError("git broke")
    monkeypatch.setattr(patch_eval, "patch_to_diff", bad)
    f = Fakes()
    r = rec(f)
    assert r["error_kind"] == "unexpected" and r["cost_usd"] == pytest.approx(0.02)
    assert not f.verify_calls


def test_infra_record_with_patch_is_reverified_without_a_model_call():
    first = rec(Fakes(status="infra"))
    assert first["error_kind"] == "infra" and first["patch"]
    f = Fakes()
    r = patch_eval.make_patch_record(job(prior=first), patch_fn=f.patch_fn,
                                     verify_fn=f.verify_fn)
    assert not f.patch_calls
    assert r["outcome"] == "pass" and r["cost_usd"] == 0.0
    assert r["reverified_from_infra"] is True
    assert r["patch"] == first["patch"] and r["diff"] == first["diff"]
    assert r["turns"] == first["turns"]
    assert f.verify_calls[0][1] == PATCH.edits


def test_infra_priors_only_keep_infra_records_with_edits():
    with_patch = rec(Fakes(status="infra"))
    no_patch = {"issue": 8, "repeat": 0, "error_kind": "infra", "patch": None}
    api = {"issue": 9, "repeat": 0, "error_kind": "api", "patch": None}
    assert patch_eval.infra_priors([with_patch, no_patch, api]) == {(7, 0): with_patch}


def test_lifetime_spend_counts_a_paid_patch_once():
    first = rec(Fakes(status="infra"))
    again = patch_eval.make_patch_record(job(prior=first), verify_fn=Fakes().verify_fn)
    assert sum(r["cost_usd"] for r in (first, again)) == pytest.approx(0.02)


# --- eligible -------------------------------------------------------------

HARNESS = {7: {"valid": True}, 8: {"valid": False, "reason": "x"}}
ROWS = {7: ROW, 8: {**ROW, "issue": 8}, 9: {**ROW, "issue": 9}}
FIXES = {7: FIX, 8: FIX, 9: FIX}


def test_eligible_drops_invalid_hypothesisless_and_other_arms():
    staged = [src(7), src(8), src(9), src(7, 1, stages={"hypothesis": None}),
              src(7, 2, arm="grounded"), src(7, 3, stages={})]
    jobs = patch_eval.eligible(staged, HARNESS, ROWS, FIXES)
    assert [(j.issue, j.repeat) for j in jobs] == [(7, 0)]
    assert jobs[0].fix is FIX and jobs[0].row is ROWS[7]


def test_eligible_raises_on_a_key_in_two_files():
    with pytest.raises(ValueError, match="duplicate"):
        patch_eval.eligible([src(7, _src="a"), src(7, _src="b")], HARNESS, ROWS, FIXES)


def test_eligible_drops_api_errored_staged_records_before_the_duplicate_check():
    retry = [src(7, error_kind="api", stages={"hypothesis": None}, _src="a"),
             src(7, cost_usd=0.5, _src="a")]
    jobs = patch_eval.eligible(retry, HARNESS, ROWS, FIXES)
    assert [(j.issue, j.repeat) for j in jobs] == [(7, 0)]
    assert jobs[0].source["cost_usd"] == 0.5


def test_eligible_keeps_the_last_of_a_repeated_key_in_one_file():
    jobs = patch_eval.eligible([src(7, cost_usd=0.1, _src="a"), src(7, cost_usd=0.2, _src="a")],
                               HARNESS, ROWS, FIXES)
    assert len(jobs) == 1 and jobs[0].source["cost_usd"] == 0.2


# --- the run --------------------------------------------------------------

def test_done_keys_skip_everything_but_api_and_infra():
    recs = [{"issue": 1, "repeat": 0, "error_kind": None},
            {"issue": 2, "repeat": 0, "error_kind": "api"},
            {"issue": 3, "repeat": 0, "error_kind": "infra"},
            {"issue": 4, "repeat": 0, "error_kind": None, "outcome": "patch_error"}]
    assert patch_eval.done_keys(recs) == {(1, 0), (4, 0)}


def test_run_patch_jobs_appends_and_resumes(tmp_path):
    p = tmp_path / "o.jsonl"
    f = Fakes()
    jobs = [job(issue=i) for i in (1, 2, 3)]
    out = patch_eval.run_patch_jobs(jobs, p, cap_dollars=5, patch_fn=f.patch_fn,
                                    verify_fn=f.verify_fn)
    assert out["ran"] == 3 and len(p.read_text().splitlines()) == 3


def test_torn_line_does_not_swallow_next_record(tmp_path):
    p = tmp_path / "o.jsonl"
    p.write_text('{"issue": 1, "rep')
    f = Fakes()
    patch_eval.run_patch_jobs([job()], p, cap_dollars=5, patch_fn=f.patch_fn,
                              verify_fn=f.verify_fn)
    assert len(patch_eval.load_patch_records(p)) == 1


def test_cap_stops_new_jobs(tmp_path):
    p = tmp_path / "o.jsonl"
    f = Fakes(run=good_run("0.50"))
    out = patch_eval.run_patch_jobs([job(issue=i) for i in range(4)], p, cap_dollars=0.4,
                                    workers=1, patch_fn=f.patch_fn, verify_fn=f.verify_fn)
    assert out["ran"] == 1 and out["stopped_for_cap"]


# --- the CLI --------------------------------------------------------------

def _cli(tmp_path, monkeypatch, staged_recs, existing=()):
    monkeypatch.setattr(patch_eval, "OUT", tmp_path / "out.jsonl")
    sf = tmp_path / "s.jsonl"
    sf.write_text("".join(json.dumps(r) + "\n" for r in staged_recs))
    monkeypatch.setattr(patch_eval, "STAGED_FILES", (sf,))
    monkeypatch.setattr(patch_eval.C, "PROVIDER", "anthropic")
    monkeypatch.setattr(patch_eval, "load_harness", lambda: HARNESS)
    monkeypatch.setattr(patch_eval, "load_inputs", lambda: (ROWS, FIXES))
    monkeypatch.setattr(patch_eval, "path_exists", lambda sha, path: True)
    (tmp_path / "out.jsonl").write_text("".join(json.dumps(r) + "\n" for r in existing))


def test_cli_refuses_when_estimate_exceeds_cap(tmp_path, monkeypatch, capsys):
    _cli(tmp_path, monkeypatch, [src(7)],
         existing=[{"issue": 99, "repeat": 0, "cost_usd": 0.545, "error_kind": None,
                    "outcome": "pass"}])
    monkeypatch.setattr(patch_eval, "run_patch_jobs",
                        lambda *a, **k: pytest.fail("must refuse before running"))
    with pytest.raises(SystemExit):
        patch_eval.main(["--max-dollars", "0.55"])
    assert "already spent" in capsys.readouterr().out


def test_cli_resume_skips_done_retries_api_and_passes_remaining_cap(tmp_path, monkeypatch):
    done = {"issue": 7, "repeat": 0, "cost_usd": 0.02, "error_kind": None, "outcome": "pass"}
    api = {"issue": 7, "repeat": 1, "cost_usd": 0.0, "error_kind": "api", "outcome": None}
    _cli(tmp_path, monkeypatch, [src(7, 0), src(7, 1)], existing=[done, api])
    seen = {}
    monkeypatch.setattr(patch_eval, "run_patch_jobs",
                        lambda jobs, path, **k: seen.update(jobs=jobs, **k) or
                        {"ran": 0, "spent": 0.0, "stopped_for_cap": False})
    patch_eval.main([])
    assert [(j.issue, j.repeat) for j in seen["jobs"]] == [(7, 1)]
    assert seen["cap_dollars"] == pytest.approx(0.55 - 0.02)


def test_cli_stale_jobs_cost_nothing_in_the_estimate(tmp_path, monkeypatch, capsys):
    _cli(tmp_path, monkeypatch, [src(7)])
    monkeypatch.setattr(patch_eval, "path_exists", lambda sha, path: False)
    monkeypatch.setattr(patch_eval, "run_patch_jobs",
                        lambda *a, **k: {"ran": 0, "spent": 0.0, "stopped_for_cap": False})
    patch_eval.main(["--max-dollars", "0.001"])
    assert "estimate $0.00" in capsys.readouterr().out


def test_cli_requires_anthropic(tmp_path, monkeypatch):
    _cli(tmp_path, monkeypatch, [src(7)])
    monkeypatch.setattr(patch_eval.C, "PROVIDER", "other")
    with pytest.raises(SystemExit):
        patch_eval.main([])


def test_cli_issues_filter(tmp_path, monkeypatch):
    _cli(tmp_path, monkeypatch, [src(7)])
    seen = {}
    monkeypatch.setattr(patch_eval, "run_patch_jobs",
                        lambda jobs, path, **k: seen.update(jobs=jobs) or
                        {"ran": 0, "spent": 0.0, "stopped_for_cap": False})
    patch_eval.main(["--issues", "99"])
    assert seen["jobs"] == []
