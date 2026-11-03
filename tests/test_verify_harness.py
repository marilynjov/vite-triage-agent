"""Harness validation logic with fake prepare/run functions (no Docker)."""
import json

import pytest

from eval import verify_harness as vh
from eval.container import TestRun
from eval.fixes import Fix

FIX = Fix(7, "f" * 40, "p" * 40, ("a/__tests__/x.spec.ts",), ("a/__tests__/x.spec.ts",),
          ("packages/vite/src/s.ts",), ("packages/vite/src/old.ts",))


def _run(status, tail="t", seconds=1.5):
    code = {"pass": 0, "test_fail": 1, "timeout": 124, "infra": 125}[status]
    return TestRun(code, seconds, tail, status == "timeout", status)


@pytest.fixture(autouse=True)
def fake_file_at(monkeypatch):
    monkeypatch.setattr(vh, "file_at", lambda sha, path: f"{sha}:{path}")


def _validate(prep=(True, ""), runs=()):
    calls = []
    it = iter(runs)

    def run_fn(fix, files, delete=(), timeout=300):
        calls.append((files, delete))
        return next(it)

    rec = vh.validate(FIX, prepare_fn=lambda f: prep, run_fn=run_fn)
    return rec, calls


def test_install_failed():
    rec, calls = _validate(prep=(False, "boom"), runs=[])
    assert (rec["valid"], rec["reason"]) == (False, "install_failed")
    assert calls == []


def test_passes_without_fix():
    rec, calls = _validate(runs=[_run("pass")])
    assert rec["reason"] == "passes_without_fix" and len(calls) == 1


def test_fails_with_fix():
    rec, _ = _validate(runs=[_run("test_fail"), _run("test_fail", "bad")])
    assert rec["reason"] == "fails_with_fix" and rec["pass_tail"] == "bad"


@pytest.mark.parametrize("runs", [["timeout"], ["test_fail", "timeout"]])
def test_timeout(runs):
    rec, _ = _validate(runs=[_run(s) for s in runs])
    assert rec["reason"] == "timeout"


@pytest.mark.parametrize("runs", [["infra"], ["test_fail", "infra"]])
def test_infra(runs):
    rec, _ = _validate(runs=[_run(s) for s in runs])
    assert rec["reason"] == "infra_error"


def test_valid_and_call_arguments():
    rec, calls = _validate(runs=[_run("test_fail", "f"), _run("pass", "p")])
    assert rec["valid"] and rec["reason"] is None
    assert (rec["issue"], rec["prefix_sha"], rec["fix_sha"]) == (7, FIX.prefix_sha, FIX.fix_sha)
    assert (rec["fail_tail"], rec["pass_tail"], rec["seconds"]) == ("f", "p", 3.0)
    (fail_files, fail_del), (pass_files, pass_del) = calls
    assert set(fail_files) == set(FIX.gold_tests) and fail_del == ()
    assert set(pass_files) == set(FIX.gold_tests) | set(FIX.fix_source)
    assert pass_del == FIX.fix_deleted
    assert all(v.startswith(FIX.fix_sha) for v in pass_files.values())


def test_main_skips_cached(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(vh, "DATA", tmp_path)
    (tmp_path / "verify_harness.jsonl").write_text(
        json.dumps({"issue": 7, "valid": True, "reason": None, "seconds": 2}) + "\n")
    monkeypatch.setattr(vh, "candidate_rows", lambda: [{"issue": 7}, {"issue": 8}])
    monkeypatch.setattr(vh, "load_commits", lambda: [])
    monkeypatch.setattr(vh, "fix_for", lambda row, commits: Fix(row["issue"], "f", "p", (), (), (), ()))
    seen = []

    def fake_validate(fix, **kw):
        seen.append(fix.issue)
        return {"issue": fix.issue, "valid": False, "reason": "timeout", "seconds": 1}

    monkeypatch.setattr(vh, "validate", fake_validate)
    monkeypatch.setattr(vh, "baseline_ids", lambda: {7})
    vh.main([])
    assert seen == [8]
    assert set(vh.load_harness()) == {7, 8}
    assert (tmp_path / "verify_valid.txt").read_text().split() == ["7"]
    assert (tmp_path / "verify_valid_new.txt").read_text().split() == []
