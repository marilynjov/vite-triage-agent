"""The run browser's loader, real-fix diff and routes: hand-made records, no model."""
from __future__ import annotations

import builtins
import json
import pathlib
import subprocess

import pytest
from fastapi.testclient import TestClient

import mining.config as M

from api import staged_runs
from api.schema import HypothesisResult, LocateResult, UnderstandResult

A = "packages/vite/src/node/a.ts"
STAGES = {
    "locate": {"reasoning": "r", "files": [{"path": A, "why": "w", "evidence": "e"}]},
    "understand": {"reasoning": "r", "regions": [
        {"path": A, "symbol": "f", "start_line": 1, "end_line": 3,
         "what_it_does": "d", "relevance": "rel"}]},
    "hypothesis": {"reasoning": "r", "issue_type": "bug", "severity": "low",
                   "suspected_components": [], "reproduction_steps": [], "confidence": 0.5,
                   "suspected_files": [A],
                   "fault": {"path": A, "start_line": 1, "end_line": 2, "mechanism": "m"}},
}
ROWS = [{"issue": 7, "title": "seven", "body": "b7"},
        {"issue": 8, "title": "eight", "body": "b8"}]


def staged(issue, repeat=0, **kw):
    rec = {"arm": "staged", "issue": issue, "repeat": repeat, "outcome": "hit",
           "failed_stage": None, "fault_hit": True, "cost_usd": 0.01, "gold": [A],
           "stages": STAGES, "stage_costs": {"locate": 0.004, "understand": 0.004,
                                             "hypothesize": 0.002},
           "error": None, "error_kind": None,
           "tool_calls": [{"name": "read_file", "input": {"path": A}, "is_error": False,
                           "output_preview": "x"}]}
    return {**rec, **kw}


def patch(issue, repeat=0, outcome="pass", cost_usd=0.004):
    return {"issue": issue, "repeat": repeat, "outcome": outcome, "cost_usd": cost_usd,
            "patch": {"reasoning": "pr", "edits": [{"path": A, "old": "a", "new": "b"}]},
            "diff": f"--- a/{A}\n+++ b/{A}\n@@ -1 +1 @@\n-a\n+b\n",  # difflib, as stored
            "error": None, "error_kind": None,
            "verify": {"passed": outcome == "pass", "exit_code": 0, "seconds": 3.5,
                       "tail": "ok", "timed_out": False, "status": outcome}}


class Files:
    def __init__(self, tmp_path):
        self.dir = tmp_path
        self.s1, self.s2 = tmp_path / "s1.jsonl", tmp_path / "s2.jsonl"
        self.p = tmp_path / "p.jsonl"

    @staticmethod
    def _write(path, recs):
        path.write_text("".join(json.dumps(r) + "\n" for r in recs))

    def staged(self, first, second=()):
        self._write(self.s1, first)
        if second:
            self._write(self.s2, second)

    def raw_staged(self, text):
        self.s1.write_text(text)

    def patches(self, recs):
        self._write(self.p, recs)


@pytest.fixture
def files(tmp_path, monkeypatch):
    f = Files(tmp_path)
    monkeypatch.setattr(staged_runs, "STAGED_FILES", (f.s1, f.s2))
    monkeypatch.setattr(staged_runs, "PATCH_FILE", f.p)
    monkeypatch.setattr(staged_runs, "val_rows", lambda split, n: ROWS)
    monkeypatch.setattr(staged_runs, "real_fix_diff", lambda row: None)
    return f


# --- loading ----------------------------------------------------------------

def test_last_record_wins_and_api_retry_is_used(files):
    files.staged([staged(7, outcome="error:understand"), staged(7, outcome="hit"),
                  staged(8, error_kind="api", outcome=None), staged(8, outcome="locate_miss")])
    runs = {(r.issue, r.repeat): r for r in staged_runs.list_runs()}
    assert runs[(7, 0)].outcome == "hit"
    assert runs[(8, 0)].outcome == "locate_miss"


def test_duplicate_key_across_files_raises(files):
    files.staged([staged(7)], [staged(7)])
    with pytest.raises(ValueError):
        staged_runs.list_runs()


def test_missing_files_give_empty_list(files):
    assert staged_runs.list_runs() == []


def test_malformed_lines_are_skipped(files):
    files.raw_staged(json.dumps(staged(7)) + "\n{not json\n\n")
    assert [r.issue for r in staged_runs.list_runs()] == [7]


def test_list_is_sorted_and_titled(files):
    files.staged([staged(8), staged(7, 1), staged(7, 0)])
    runs = staged_runs.list_runs()
    assert [(r.issue, r.repeat) for r in runs] == [(7, 0), (7, 1), (8, 0)]
    assert runs[0].title == "seven"


def test_summary_without_patch(files):
    files.staged([staged(7, cost_usd=0.01)])
    (r,) = staged_runs.list_runs()
    assert (r.patch_outcome, r.cost_usd) == (None, 0.01)


def test_summary_cost_includes_the_patch(files):
    files.staged([staged(7, cost_usd=0.01)])
    files.patches([patch(7, outcome="pass", cost_usd=0.004)])
    (r,) = staged_runs.list_runs()
    assert r.patch_outcome == "pass" and r.cost_usd == pytest.approx(0.014)


def test_last_patch_record_wins(files):
    files.staged([staged(7)])
    files.patches([{**patch(7), "outcome": None, "error_kind": "infra"}, patch(7, outcome="test_fail")])
    assert staged_runs.list_runs()[0].patch_outcome == "test_fail"


def test_detail_joins_patch_and_validates_stages(files):
    files.staged([staged(7)])
    files.patches([patch(7, outcome="test_fail")])
    d = staged_runs.get_run(7, 0)
    assert d.body == "b7" and d.gold == [A]
    assert d.stages.hypothesis.fault.path == A
    assert d.patch.verify.status == "test_fail" and d.patch.edits[0].path == A
    assert d.tool_calls[0].name == "read_file"


def test_detail_without_patch_record(files):
    files.staged([staged(7)])
    assert staged_runs.get_run(7, 0).patch is None


def test_invalid_stage_becomes_none(files):
    files.staged([staged(7, stages={"locate": {"bogus": 1}, "understand": None,
                                    "hypothesis": None})])
    assert staged_runs.get_run(7, 0).stages.locate is None


def test_unknown_key_is_none(files):
    assert staged_runs.get_run(999, 0) is None


def test_issue_missing_from_val_has_empty_title_and_no_fix(files, monkeypatch):
    calls = []
    monkeypatch.setattr(staged_runs, "real_fix_diff", lambda row: calls.append(row))
    files.staged([staged(42)])
    d = staged_runs.get_run(42, 0)
    assert (d.title, d.body, d.real_fix, calls) == ("", "", None, [])


def test_every_stage_in_the_real_files_validates():
    runs = staged_runs.load_runs()
    if not runs:
        pytest.skip("data/runs/ not populated")
    for rec in runs.values():
        for name, model in (("locate", LocateResult), ("understand", UnderstandResult),
                            ("hypothesis", HypothesisResult)):
            if rec["stages"][name] is not None:
                model.model_validate(rec["stages"][name])


# --- the real fix's diff -----------------------------------------------------

SPEC = "packages/vite/src/node/__tests__/a.spec.ts"


class Repo:
    def __init__(self, path):
        self.path = path
        self._git("init", "-q")
        self._git("config", "user.email", "t@t")
        self._git("config", "user.name", "t")
        self.base_sha = self.commit({A: "one\n", SPEC: "t1\n"})
        self.fix_sha = self.commit({A: "two\n", SPEC: "t2\n"})

    def _git(self, *args):
        return subprocess.run(["git", "-C", str(self.path), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, files):
        for rel, text in files.items():
            p = self.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self._git("add", "-A")
        self._git("commit", "-qm", "c")
        return self._git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    r = Repo(tmp_path)
    monkeypatch.setattr(M, "REPO_PATH", tmp_path)
    staged_runs._cached_diff.cache_clear()
    yield r
    staged_runs._cached_diff.cache_clear()


def test_real_fix_diff_excludes_test_files(repo):
    fix = staged_runs._diff_at(repo.fix_sha)
    assert (fix.sha, fix.prefix_sha) == (repo.fix_sha, repo.base_sha)
    assert "node/a.ts" in fix.diff and "+two" in fix.diff
    assert "__tests__" not in fix.diff


def test_fix_touching_only_tests_gives_empty_diff(repo):
    sha = repo.commit({SPEC: "t3\n"})
    assert staged_runs._diff_at(sha).diff == ""


def test_git_failure_gives_none(repo):
    assert staged_runs._diff_at("0" * 40) is None


def test_row_with_no_fix_commit_gives_none(monkeypatch):
    monkeypatch.setattr(staged_runs, "_commits", lambda: [])
    assert staged_runs.real_fix_diff({"issue": 1, "linked_prs": []}) is None


def test_falls_back_to_first_fix_commit(repo, monkeypatch):
    sha = repo.commit({A: "three\n"})  # no unit test, so fix_for finds nothing
    monkeypatch.setattr(staged_runs, "_commits", lambda: [{"sha": sha, "closes_refs": [5]}])
    fix = staged_runs.real_fix_diff({"issue": 5, "linked_prs": []})
    assert fix.sha == sha and "+three" in fix.diff


def test_prefers_the_commit_the_verifier_graded(repo, monkeypatch):
    later = repo.commit({A: "four\n"})
    commits = [{"sha": later, "closes_refs": [5]}, {"sha": repo.fix_sha, "closes_refs": [5]}]
    monkeypatch.setattr(staged_runs, "_commits", lambda: commits)
    assert staged_runs.real_fix_diff({"issue": 5, "linked_prs": []}).sha == repo.fix_sha


# --- routes -----------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    async def noop():
        pass

    monkeypatch.setattr("api.main.db.connect", noop)
    monkeypatch.setattr("api.main.db.disconnect", noop)
    from api.main import app
    with TestClient(app) as c:
        yield c


def test_list_endpoint(client, files):
    files.staged([staged(8), staged(7, repeat=1)])
    body = client.get("/staged-runs").json()
    assert [(r["issue"], r["repeat"]) for r in body] == [(7, 1), (8, 0)]


def test_detail_endpoint_and_404(client, files):
    files.staged([staged(7)])
    assert client.get("/staged-runs/7/0").json()["stages"]["locate"]["files"]
    r = client.get("/staged-runs/7/3")
    assert r.status_code == 404 and "issue 7 repeat 3" in r.json()["detail"]


def test_loader_never_opens_a_test_split_path(client, tmp_path, monkeypatch):
    # set up by hand, not via `files`: the guard needs the real val_rows and real_fix_diff
    sf = tmp_path / "s.jsonl"
    sf.write_text(json.dumps(staged(17676)) + "\n")
    monkeypatch.setattr(staged_runs, "STAGED_FILES", (sf,))
    monkeypatch.setattr(staged_runs, "PATCH_FILE", tmp_path / "none.jsonl")
    opened = []
    real_open, real_read = builtins.open, pathlib.Path.read_text

    def spy_open(p, *a, **k):
        opened.append(str(p))
        return real_open(p, *a, **k)

    def spy_read(self, *a, **k):
        opened.append(str(self))
        return real_read(self, *a, **k)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(pathlib.Path, "read_text", spy_read)
    assert client.get("/staged-runs").status_code == 200
    assert client.get("/staged-runs/17676/0").status_code == 200
    assert any("splits/val" in p for p in opened)
    assert not [p for p in opened if "splits/test" in p]


def test_a_git_failure_is_not_cached(repo, monkeypatch):
    real = staged_runs._git
    calls = []

    def flaky(*args):
        calls.append(args)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("git", 60)
        return real(*args)

    monkeypatch.setattr(staged_runs, "_git", flaky)
    assert staged_runs._diff_at(repo.fix_sha) is None
    assert staged_runs._diff_at(repo.fix_sha).sha == repo.fix_sha
