"""GET /eval/summary: one page of numbers from stored files, no model call, never a 500."""
import builtins
import json
import pathlib

import pytest
from fastapi.testclient import TestClient

from api import eval_summary as E


def jsonl(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return path


def arm(a, issue, r1, *, clean=True, cost=0.001, error_kind=None):
    return {"arm": a, "issue": issue, "repeat": 0, "recall_at_1": r1, "recall_at_5": r1,
            "rr": r1, "mentions_gold_path": not clean, "cost_usd": cost, "latency_ms": 10,
            "error_kind": error_kind}


def staged(issue, outcome, repeat=0):
    return {**arm("staged", issue, 0.0), "repeat": repeat, "outcome": outcome}


def patch(issue, outcome, repeat=0):
    return {"issue": issue, "repeat": repeat, "outcome": outcome, "source_outcome": "hit",
            "source_fault_hit": None, "cost_usd": 0.005, "error_kind": None}


def night(at, ok=True, value=0.685):
    m = {"recall@1": 0.2, "recall@5": 0.5, "recall@10": value, "mrr": 0.4} if ok else {}
    return {"at": at, "ok": ok, "pin": "p", "strategy": "fixed_v1_header", "n": 206,
            "clean_n": 168, "all": m, "clean": m, "seconds": 30.0, "changed": False,
            "error": None if ok else "RuntimeError: db down"}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    p = {"arms": tmp_path / "arms.jsonl", "staged": tmp_path / "staged.jsonl",
         "patch": tmp_path / "patch.jsonl", "nightly": tmp_path / "nightly.jsonl",
         "labels": tmp_path / "labels.jsonl"}
    monkeypatch.setattr(E, "ARMS_FILE", p["arms"])
    monkeypatch.setattr(E, "STAGED_FILE", p["staged"])
    monkeypatch.setattr(E, "PATCH_FILE", p["patch"])
    monkeypatch.setattr(E, "NIGHTLY_FILE", p["nightly"])
    monkeypatch.setattr(E, "LABELS_FILE", p["labels"])
    return p


@pytest.fixture
def client(monkeypatch):
    async def noop():
        pass

    monkeypatch.setattr("api.main.db.connect", noop)
    monkeypatch.setattr("api.main.db.disconnect", noop)
    from api.main import app
    with TestClient(app) as c:
        yield c


def test_every_section_from_its_file(client, paths):
    jsonl(paths["arms"], [arm("agent", 1, 1.0), arm("agent", 2, 0.0), arm("agent", 3, 1.0, clean=False),
                          arm("agent", 4, 0.0, error_kind="api"), arm("retrieval", 1, 0.0, cost=0.0)])
    jsonl(paths["staged"], [staged(1, "hit"), staged(2, "hit"), staged(3, "locate_miss"),
                            staged(4, "understand_miss")])
    jsonl(paths["patch"], [patch(1, "pass"), patch(2, "test_fail"), patch(3, "stale_path")])
    jsonl(paths["nightly"], [night("2026-11-22T03:00:00+00:00"), night("2026-11-23T03:00:00+00:00", ok=False)])
    jsonl(paths["labels"], [{"issue": 1, "repeat": 0, "category": "a", "note": ""},
                            {"issue": 2, "repeat": 0, "category": "a", "note": ""}])
    s = client.get("/eval/summary").json()

    assert s["arms_source"] == "arms.jsonl"
    assert [a["arm"] for a in s["arms"]] == ["retrieval", "agent"]  # fixed order, absent arms left out
    agent = s["arms"][1]
    assert agent["runs"] == 2 and agent["recall_at_1"] == 0.5 and agent["per_triage"] == pytest.approx(0.001)
    assert {o["name"]: o["share"] for o in s["staged_outcomes"]} == {
        "hit": 0.5, "locate_miss": 0.25, "understand_miss": 0.25}
    assert {o["name"]: o["n"] for o in s["patch_outcomes"]} == {"pass": 1, "test_fail": 1, "stale_path": 1}
    assert s["patch_pass_rate"] == 0.5  # 1 pass of 2 patchable
    ok, failed = s["nightly"]
    assert ok["recall_at_10_clean"] == 0.685 and ok["mrr_clean"] == 0.4 and ok["ok"] is True
    assert failed["ok"] is False and failed["recall_at_10_clean"] is None and "db down" in failed["error"]
    assert s["taxonomy"] == [{"category": "a", "n": 2}]


def test_missing_files_give_empty_sections_not_a_500(client, paths):
    r = client.get("/eval/summary")
    assert r.status_code == 200
    s = r.json()
    assert s == {"nightly": [], "arms_source": "arms.jsonl", "arms": [], "staged_outcomes": [],
                 "patch_outcomes": [], "patch_pass_rate": None, "taxonomy": []}


def test_one_bad_file_empties_only_its_section(client, paths):
    jsonl(paths["labels"], [{"issue": 1, "repeat": 0, "category": "", "note": ""}])  # invalid label
    jsonl(paths["staged"], [staged(1, "hit")])
    s = client.get("/eval/summary").json()
    assert s["taxonomy"] == [] and s["staged_outcomes"] == [{"name": "hit", "n": None, "share": 1.0}]


def test_nightly_keeps_the_newest_30_oldest_first(paths):
    jsonl(paths["nightly"], [night(f"2026-10-{d:02d}T03:00:00+00:00") for d in range(1, 32)])
    pts = E.summary().nightly
    assert len(pts) == 30 and pts[0].at.startswith("2026-10-02") and pts[-1].at.startswith("2026-10-31")


def test_the_summary_never_opens_a_test_split_path(client, paths, monkeypatch):
    opened = []
    real_open, real_read = builtins.open, pathlib.Path.read_text
    monkeypatch.setattr(builtins, "open", lambda p, *a, **k: (opened.append(str(p)), real_open(p, *a, **k))[1])
    monkeypatch.setattr(pathlib.Path, "read_text",
                        lambda self, *a, **k: (opened.append(str(self)), real_read(self, *a, **k))[1])
    jsonl(paths["arms"], [arm("agent", 1, 1.0)])
    assert client.get("/eval/summary").status_code == 200
    assert not [p for p in opened if "splits/test" in p]
