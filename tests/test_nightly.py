"""The nightly retrieval eval: one record a run, a moved metric exits 1, a failure exits 2.
`evaluate` is injected, so these need no database and no Ollama."""
import builtins
from pathlib import Path

from eval import nightly


def fake(value=0.5, n=None):
    def evaluate(rows, k_values, strategy):
        m = {"recall@1": value, "recall@5": value, "recall@10": value, "mrr": value}
        return {"n": n or len(rows), "clean_n": n or len(rows), "seconds": 1.0, "all": m, "clean": m}
    return evaluate


def boom(rows, k_values, strategy):
    raise RuntimeError("db down")


def test_first_record_is_a_baseline_and_exits_0(tmp_path):
    path = tmp_path / "nightly" / "retrieve.jsonl"
    assert nightly.run(evaluate=fake(), path=path) == 0
    (rec,) = nightly.load_history(path)
    assert rec["ok"] is True and rec["changed"] is False and rec["error"] is None
    assert rec["strategy"] == nightly.STRATEGY == "fixed_v1_header"
    assert rec["clean"]["recall@10"] == 0.5 and rec["n"] > 200 and rec["pin"]


def test_same_numbers_exit_0_and_a_moved_metric_exits_1(tmp_path):
    path = tmp_path / "h.jsonl"
    assert nightly.run(evaluate=fake(), path=path) == 0
    assert nightly.run(evaluate=fake(), path=path) == 0
    assert nightly.run(evaluate=fake(0.51), path=path) == 1
    assert [r["changed"] for r in nightly.load_history(path)] == [False, False, True]


def test_a_failure_is_recorded_exits_2_and_is_never_a_baseline(tmp_path):
    path = tmp_path / "h.jsonl"
    assert nightly.run(evaluate=fake(), path=path) == 0
    assert nightly.run(evaluate=boom, path=path) == 2
    failed = nightly.load_history(path)[-1]
    assert failed["ok"] is False and "db down" in failed["error"]
    assert failed["all"] == {} and failed["clean"] == {} and failed["changed"] is False
    assert nightly.run(evaluate=fake(), path=path) == 0  # compared with the ok record


def test_a_new_pin_strategy_or_n_is_a_new_baseline_not_a_change():
    m = {"recall@10": 0.5}
    cur = {"ok": True, "pin": "a", "strategy": "s", "n": 206, "all": m, "clean": m}
    assert nightly.compare(None, cur) is False
    assert nightly.compare({**cur, "pin": "b", "clean": {"recall@10": 0.1}}, cur) is False
    assert nightly.compare({**cur, "strategy": "t", "clean": {"recall@10": 0.1}}, cur) is False
    assert nightly.compare({**cur, "n": 40, "clean": {"recall@10": 0.1}}, cur) is False
    assert nightly.compare({**cur, "clean": {"recall@10": 0.1}}, cur) is True


def test_a_missing_history_is_empty(tmp_path):
    assert nightly.load_history(tmp_path / "none.jsonl") == []


def test_nightly_reads_only_the_val_split(tmp_path, monkeypatch):
    seen = []
    real_open, real_read = builtins.open, Path.read_text
    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (seen.append(str(f)), real_open(f, *a, **k))[1])
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (seen.append(str(self)), real_read(self, *a, **k))[1])
    nightly.run(evaluate=fake(), path=tmp_path / "h.jsonl")
    assert any(s.endswith("val.jsonl") for s in seen)
    assert not [s for s in seen if "splits/test" in s or "splits/train" in s]
