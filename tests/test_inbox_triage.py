"""inbox.triage: one issue at a chosen commit, its comment and its record. Model and search faked."""
import json
from types import SimpleNamespace as NS

import pytest

from agent import tools
from agent.loop import Run, RunError
from api.schema import GroundedTriageResult
from inbox import triage as T
from inbox.github import Issue

ISSUE = Issue(7, "Score is wrong", "after a strike", "2026-01-02T00:00:00Z",
              "https://github.com/acme/widgets/issues/7")


def _result(files=("src/a.py",), issue_type="bug", confidence=0.8, reasoning="because"):
    return GroundedTriageResult(reasoning=reasoning, issue_type=issue_type, severity="medium",
                                suspected_components=[], reproduction_steps=[],
                                confidence=confidence, suspected_files=list(files))


def _run(result, cost="0.002"):
    from decimal import Decimal
    turn = NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0,
              cache_creation_input_tokens=0, cost_usd=Decimal(cost))
    call = NS(name="grep")
    return Run(result=result, turns=[turn, turn], trace=[call, call, NS(name="read_file")])


def search(query, **kw):
    return ["hit"]


def test_triage_returns_the_agents_files_and_cost():
    o = T.triage(ISSUE, "abc", search_fn=search, run_fn=lambda *a, **k: _run(_result()))
    assert (o.issue, o.sha, o.files, o.issue_type, o.confidence) == (7, "abc", ["src/a.py"], "bug", 0.8)
    assert o.turns == 2 and o.tools == {"grep": 2, "read_file": 1}
    assert o.cost_usd == pytest.approx(0.004) and o.error is None


def test_triage_reads_at_the_given_sha_with_the_issue_date_as_cutoff():
    seen = {}

    def run_fn(title, body, candidates, cutoff, **kw):
        seen.update(rev=tools.current_rev(), cutoff=cutoff, candidates=candidates)
        return _run(_result())

    T.triage(ISSUE, "abc123", search_fn=search, run_fn=run_fn)
    assert seen == {"rev": "abc123", "cutoff": "2026-01-02T00:00:00Z", "candidates": ["hit"]}


def test_a_failed_run_is_an_outcome_with_its_cost():
    def run_fn(*a, **k):
        raise RunError("tokens budget reached", _run(None, cost="0.01"))

    o = T.triage(ISSUE, "abc", search_fn=search, run_fn=run_fn)
    assert o.error == "tokens budget reached" and o.files == []
    assert o.cost_usd == pytest.approx(0.02) and o.turns == 2


@pytest.mark.parametrize("kw,expected", [
    ({"error": "x"}, False),
    ({"issue_type": "question"}, False),
    ({"issue_type": "enhancement"}, False),
    ({"confidence": 0.4}, False),
    ({}, True),
])
def test_recommend_rules(kw, expected):
    base = dict(issue=7, sha="s", files=["a"], issue_type="bug", confidence=0.8, reasoning="r",
                turns=1, tools={}, cost_usd=0.0, error=None)
    assert T.recommend(T.Outcome(**{**base, **kw})) is expected


def _outcome(**kw):
    base = dict(issue=7, sha="abc", files=["src/a.py", "src/b.py"], issue_type="bug",
                confidence=0.8, reasoning="because", turns=2, tools={"grep": 2}, cost_usd=0.0021,
                error=None)
    return T.Outcome(**{**base, **kw})


def test_render_links_files_at_the_sha_and_ends_with_the_marker():
    text = T.render(_outcome(), ISSUE, "acme", "widgets")
    assert "1. [`src/a.py`](https://github.com/acme/widgets/blob/abc/src/a.py)" in text
    assert "2. [`src/b.py`](https://github.com/acme/widgets/blob/abc/src/b.py)" in text
    assert "because" in text and "bug" in text and "$0.0021" in text
    assert text.rstrip().endswith(T.MARKER) and "may be wrong" in text


def test_render_truncates_long_reasoning_to_1500():
    text = T.render(_outcome(reasoning="x" * 4000), ISSUE, "acme", "widgets")
    assert "x" * 1500 in text and "x" * 1501 not in text


def test_record_appends_one_line_per_call(tmp_path):
    path = tmp_path / "live" / "runs.jsonl"
    T.record(_outcome(), None, path)
    T.record(_outcome(issue=8), "https://github.com/acme/widgets/issues/8#c1", path)
    lines = [json.loads(l) for l in path.read_text().splitlines()]
    assert [l["issue"] for l in lines] == [7, 8]
    assert lines[0]["posted_url"] is None and lines[1]["posted_url"].endswith("#c1")
    assert {"files", "sha", "cost_usd", "error", "at"} <= set(lines[0])
