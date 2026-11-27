"""inbox.score: live runs against the fixes' files, once the miner has labelled them."""
import pytest

from inbox.score import score


def run(issue, files, *, error=None, at="2026-01-01T00:00:00+00:00"):
    return {"issue": issue, "files": files, "error": error, "at": at}


def row(issue, gold):
    return {"issue": issue, "gold_files": gold}


def test_score_uses_the_latest_successful_run():
    runs = [run(1, ["a"], at="t1"), run(1, ["b"], at="t2"), run(1, [], error="budget", at="t3"),
            run(2, ["x", "y"])]
    s = score(runs, [row(1, ["b"]), row(2, ["y"])])
    assert s["n"] == 2
    assert s["recall_at_1"] == pytest.approx(0.5) and s["recall_at_5"] == pytest.approx(1.0)
    assert s["mrr"] == pytest.approx(0.75) and s["waiting"] == []


def test_issues_without_a_label_are_waiting():
    s = score([run(1, ["a"]), run(3, ["c"])], [row(1, ["a"])])
    assert s["n"] == 1 and s["waiting"] == [3]


def test_nothing_to_score_is_zero_not_an_error():
    assert score([], []) == {"n": 0, "recall_at_1": 0.0, "recall_at_5": 0.0, "mrr": 0.0,
                             "waiting": []}
