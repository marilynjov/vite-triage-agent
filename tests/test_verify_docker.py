"""Fail-to-pass verify for #14515 in the real container (RUN_DOCKER=1)."""
import json

import pytest

import mining.config as M
from eval import container, fixes, verify

pytestmark = pytest.mark.docker


@pytest.fixture(scope="module")
def fix():
    commits = [json.loads(l) for l in (M.RAW / "commits.jsonl").read_text().splitlines() if l]
    rows = [json.loads(l) for l in (M.DATA / "splits" / "val.jsonl").read_text().splitlines() if l]
    f = fixes.fix_for(next(r for r in rows if r["issue"] == 14515), commits)
    container.ensure_image()
    ok, tail = container.prepare(f)
    assert ok, tail
    return f


def _gold(fix):
    return {p: fixes.file_at(fix.fix_sha, p) for p in fix.gold_tests}


def _with_fix(fix):
    return {**{p: fixes.file_at(fix.fix_sha, p) for p in fix.fix_source}, **_gold(fix)}


def test_fail_then_pass_then_fail_again(fix):
    empty = verify.verify(fix, [])
    assert empty.status == "test_fail" and empty.passed is False, empty.tail
    assert "Test Files" in empty.tail, "vitest never ran the tests: " + empty.tail

    with container.LOCK:
        real = container.run_tests(fix, _with_fix(fix), fix.fix_deleted)
    assert real.exit_code == 0 and real.status == "pass", real.tail

    again = verify.verify(fix, [])
    assert again.status == "test_fail", "reset did not undo the previous run's fix: " + again.tail
    assert "Test Files" in again.tail, again.tail


def test_timeout_is_reported_and_does_not_leak(fix):
    with container.LOCK:
        slow = container.run_tests(fix, _with_fix(fix), fix.fix_deleted, timeout=1)
    assert slow.timed_out is True and slow.status == "timeout"
    after = verify.verify(fix, [])
    assert after.status == "test_fail" and not after.timed_out, after.tail
    assert "Test Files" in after.tail, after.tail


def test_a_missing_base_is_infra_not_a_test_failure(fix):
    ghost = fixes.Fix(0, fix.fix_sha, fix.prefix_sha, fix.gold_tests, fix.test_paths, (), ())
    with container.LOCK:
        run = container.run_tests(ghost, _gold(fix))
    assert run.status == "infra" and run.exit_code != 0, run.tail
