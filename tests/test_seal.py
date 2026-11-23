"""The test-split seal: two declared runs, a clean tree, the commit recorded, asked first.
Nothing here reads data/splits/test.jsonl: val_rows is always stubbed."""
import json

import pytest

from eval import ablation, seal


def _open(path, name="test-final", *, clean=True, head="abc123"):
    return seal.open_test(name, path=path, clean=lambda: clean, head=lambda: head,
                          now="2026-11-23T12:00:00+00:00")


def test_an_undeclared_name_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="not a declared test run"):
        _open(tmp_path / "seal.json", "test-retry")
    assert not (tmp_path / "seal.json").exists()


def test_first_open_records_commit_and_time(tmp_path):
    entry = _open(tmp_path / "seal.json")
    assert entry == {"opened_at": "2026-11-23T12:00:00+00:00", "commit": "abc123"}
    assert json.loads((tmp_path / "seal.json").read_text()) == {"test-final": entry}


def test_a_resume_keeps_the_first_commit(tmp_path):
    _open(tmp_path / "seal.json")
    again = _open(tmp_path / "seal.json", head="def456", clean=False)  # resume: tree state irrelevant
    assert again["commit"] == "abc123"
    _open(tmp_path / "seal.json", "test-staged-n40", head="def456")
    assert set(json.loads((tmp_path / "seal.json").read_text())) == {"test-final", "test-staged-n40"}


def test_a_dirty_tree_is_refused_on_first_open(tmp_path):
    with pytest.raises(SystemExit, match="uncommitted"):
        _open(tmp_path / "seal.json", clean=False)
    assert not (tmp_path / "seal.json").exists()


@pytest.fixture
def cli(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ablation, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(ablation.C, "PROVIDER", "anthropic")
    monkeypatch.setattr(seal, "open_test", lambda name, **k: calls.append(("seal", name))
                        or {"commit": "abc1234", "opened_at": "t"})
    monkeypatch.setattr(ablation, "val_rows",
                        lambda split, n, issues=None: calls.append(("rows", split)) or [])
    return calls


@pytest.mark.parametrize("argv", [
    ["--arms", "retrieval", "--split", "test", "--name", "test-final"],
    ["--arms", "retrieval", "--split", "val", "--open-test", "--name", "x"],
])
def test_ablation_refuses_test_without_the_flag_and_the_flag_without_test(cli, argv):
    with pytest.raises(SystemExit):
        ablation.main(argv)
    assert cli == []


def test_ablation_asks_the_seal_before_reading_test(cli):
    ablation.main(["--arms", "retrieval", "--split", "test", "--open-test", "--name", "test-final",
                   "--n", "100000", "--repeats", "1"])
    assert cli == [("seal", "test-final"), ("rows", "test")]


def test_the_two_declared_runs():
    assert seal.TEST_RUNS == ("test-final", "test-staged-n40")
    assert seal.SEAL.name == "TEST_OPENED.json" and seal.SEAL.parent.name == "splits"
