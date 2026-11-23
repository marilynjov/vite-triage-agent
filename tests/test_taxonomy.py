"""The failure taxonomy's mechanics: which runs are failures, a fixed sample, label rules."""
import json

import pytest

from eval import taxonomy


def rec(issue, *, arm="agent", r1=0.0, clean=True, error_kind=None, repeat=0):
    return {"arm": arm, "issue": issue, "repeat": repeat, "recall_at_1": r1,
            "mentions_gold_path": not clean, "error_kind": error_kind}


def test_failures_are_clean_agent_misses_that_were_not_api_errors():
    recs = [rec(1), rec(2, r1=1.0), rec(3, clean=False), rec(4, error_kind="api"),
            rec(5, arm="grounded"), rec(6, error_kind="run"), rec(7, r1=0.5)]
    assert [r["issue"] for r in taxonomy.failures(recs)] == [1, 6]


def test_the_sample_is_fixed_and_sorted_by_issue():
    recs = [rec(i) for i in range(100, 0, -1)]
    a = taxonomy.sample_failures(recs, n=20)
    assert a == taxonomy.sample_failures(list(reversed(recs)), n=20)
    assert [r["issue"] for r in a] == sorted(r["issue"] for r in a) and len(a) == 20


def test_too_few_failures_raises():
    with pytest.raises(ValueError, match="only 3 failures"):
        taxonomy.sample_failures([rec(i) for i in range(3)], n=20)


def write(path, lines):
    path.write_text("".join(json.dumps(l) + "\n" for l in lines))
    return path


def test_labels_load_and_reject_empty_categories_and_duplicates(tmp_path):
    assert taxonomy.load_labels(tmp_path / "none.jsonl") == []
    good = {"issue": 1, "repeat": 0, "category": "a", "note": "n"}
    assert taxonomy.load_labels(write(tmp_path / "ok.jsonl", [good])) == [good]
    with pytest.raises(ValueError, match="no category"):
        taxonomy.load_labels(write(tmp_path / "e.jsonl", [{**good, "category": " "}]))
    with pytest.raises(ValueError, match="duplicate"):
        taxonomy.load_labels(write(tmp_path / "d.jsonl", [good, good]))


def test_counts_order_by_n_then_name():
    labels = [{"category": c} for c in ("c", "b", "a", "b")]
    assert taxonomy.counts(labels) == [("b", 2), ("a", 1), ("c", 1)]
