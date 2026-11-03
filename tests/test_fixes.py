"""Fix lookup against the real Vite clone (free: git only)."""
import json

import pytest

import mining.config as M
from eval import fixes

SRC = "packages/vite/src/"


@pytest.fixture(scope="module")
def commits():
    return [json.loads(l) for l in (M.RAW / "commits.jsonl").read_text().splitlines() if l]


@pytest.fixture(scope="module")
def val():
    return [json.loads(l) for l in (M.DATA / "splits" / "val.jsonl").read_text().splitlines() if l]


def _row(val, issue):
    return next(r for r in val if r["issue"] == issue)


def test_14515_fix_is_72f6a52_with_one_spec_and_one_source(val, commits):
    fix = fixes.fix_for(_row(val, 14515), commits)
    assert fix.issue == 14515
    assert fix.fix_sha.startswith("72f6a52")
    assert fix.prefix_sha.startswith("cf80e40")
    assert fix.test_paths == ("packages/vite/src/node/__tests__/plugins/css.spec.ts",)
    assert fix.gold_tests == fix.test_paths
    assert fix.fix_source == ("packages/vite/src/node/plugins/css.ts",)
    assert fix.fix_deleted == ()


def test_13907_changed_no_unit_test(val, commits):
    assert fixes.fix_for(_row(val, 13907), commits) is None


def test_deleted_source_paths_land_in_fix_deleted(val, commits):
    found = [f for f in (fixes.fix_for(r, commits) for r in val) if f and f.fix_deleted]
    if not found:
        pytest.skip("no val fix with a unit test deletes a file under packages/vite/src")
    fix = found[0]
    out = fixes._git("diff", "--name-status", "--no-renames", fix.prefix_sha, fix.fix_sha).stdout
    deleted = {l.split("\t")[1] for l in out.splitlines()
               if l.startswith("D\t") and l.split("\t")[1].startswith(SRC)}
    assert set(fix.fix_deleted) == deleted
    assert not deleted & set(fix.fix_source) and not deleted & set(fix.gold_tests)


def test_file_at_reads_a_blob_at_a_commit(val, commits):
    fix = fixes.fix_for(_row(val, 14515), commits)
    before = fixes.file_at(fix.prefix_sha, fix.fix_source[0])
    after = fixes.file_at(fix.fix_sha, fix.fix_source[0])
    assert before and after and before != after
