"""Gold regions against the real Vite clone (free: git only)."""
import json

import pytest

import mining.config as M
from agent import tools
from eval import gold_regions as gr

ESBUILD = "packages/vite/src/node/plugins/esbuild.ts"
ROW_13907 = {"issue": 13907, "linked_prs": [13909], "gold_files": [ESBUILD]}
# PR 14094 replaced 13909's regexes with string manipulation, so 13907's own
# lines no longer survive at the pin; 14094's do, in the same file.
ROW_14065 = {"issue": 14065, "linked_prs": [14094], "gold_files": [ESBUILD]}


@pytest.fixture(scope="module")
def commits():
    return [json.loads(l) for l in (M.RAW / "commits.jsonl").read_text().splitlines() if l]


def _by_pr(commits, pr):
    return next(c for c in commits if pr in c["pr_refs"])


def test_fix_commits_for_13907_include_the_13909_commit(commits):
    shas = gr.fix_commits(ROW_13907, commits)
    assert _by_pr(commits, 13909)["sha"] in shas


def test_fix_commits_match_closes_refs(commits):
    c = next(c for c in commits if c["closes_refs"])
    row = {"issue": c["closes_refs"][0], "linked_prs": [], "gold_files": []}
    assert c["sha"] in gr.fix_commits(row, commits)


def test_13907_fix_was_rewritten_later_so_has_no_regions(commits):
    assert gr.regions_for(ROW_13907, commits) == {ESBUILD: []}


def test_regions_read_back_from_the_pinned_file(commits):
    ranges = gr.regions_for(ROW_14065, commits)[ESBUILD]
    assert ranges
    n = tools.file_length(ESBUILD)
    for start, end in ranges:
        assert 1 <= start <= end <= n
        assert tools.read_file(ESBUILD, start, end).split("\n", 1)[1].strip()


def test_union_over_several_fix_commits(commits):
    # PR 15025 wrote line 34 and PR 15059 line 35 of htmlFallback.ts; the union
    # is grouped after merging, so the two adjacent lines form one range.
    path = "packages/vite/src/node/server/middlewares/htmlFallback.ts"
    row = lambda prs: {"issue": 0, "linked_prs": prs, "gold_files": [path]}
    assert gr.regions_for(row([15025]), commits)[path] == [[34, 34]]
    assert gr.regions_for(row([15059]), commits)[path] == [[35, 35]]
    assert gr.regions_for(row([15025, 15059]), commits)[path] == [[34, 35]]


def test_deletion_only_fix_has_no_regions(commits, monkeypatch):
    other = "f" * 40
    monkeypatch.setattr(gr, "_blame", lambda path: [(other, n) for n in range(1, 20)])
    assert gr.regions_for(ROW_13907, commits) == {ESBUILD: []}


def test_gold_file_absent_at_the_pin_maps_to_empty(commits):
    row = dict(ROW_13907, gold_files=["no/such/file.ts"])
    assert gr.regions_for(row, commits) == {"no/such/file.ts": []}


def test_group_makes_inclusive_contiguous_ranges():
    assert gr._group([5, 3, 4, 9, 3]) == [[3, 5], [9, 9]]


def test_cache_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "CACHE", tmp_path / "gold_regions.jsonl")
    calls = []
    real = gr._blame
    monkeypatch.setattr(gr, "_blame", lambda p: calls.append(p) or real(p))
    first = gr.load_regions([ROW_14065])
    assert first[14065][ESBUILD] and calls
    calls.clear()
    assert gr.load_regions([ROW_14065]) == first
    assert calls == []


def test_bad_repo_path_raises(monkeypatch):
    monkeypatch.setattr(M, "REPO_PATH", M.ROOT / "no-such-clone")
    with pytest.raises(Exception):
        gr._blame(ESBUILD)


def test_bad_pin_raises(tmp_path, monkeypatch):
    pin = tmp_path / "pin.txt"
    pin.write_text("0" * 40)
    monkeypatch.setattr(M, "PINNED_SHA", pin)
    with pytest.raises(RuntimeError):
        gr._blame(ESBUILD)


def test_cache_entry_with_other_or_no_pin_is_rebuilt(tmp_path, monkeypatch):
    cache = tmp_path / "gold_regions.jsonl"
    monkeypatch.setattr(gr, "CACHE", cache)
    stale = {"issue": 14065, "regions": {ESBUILD: []}}
    cache.write_text(json.dumps(stale) + "\n")  # no pin
    got = gr.load_regions([ROW_14065])
    assert got[14065][ESBUILD]
    cache.write_text(json.dumps(dict(stale, pin="0" * 40)) + "\n")  # other pin
    assert gr.load_regions([ROW_14065])[14065][ESBUILD]
    assert json.loads(cache.read_text())["pin"] == gr._pin()
    assert not cache.with_name(cache.name + ".tmp").exists()
