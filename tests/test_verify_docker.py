"""Fail-to-pass verify for #14515 in the real container (RUN_DOCKER=1)."""
import dataclasses
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
    try:
        assert ok, tail
        yield f
    finally:  # one base at a time: never leave 2-3 GB behind
        container.discard(f)


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


SLOW = "packages/vite/src/node/__tests__/zz-slow.spec.ts"
SLOW_SPEC = ("import { test } from 'vitest'\n"
             "test('sleeps', async () => { await new Promise((r) => setTimeout(r, 4000)) }, 60000)\n")


def test_timeout_is_reported_and_does_not_leak(fix):
    # A spec that sleeps, not the real tests: run without pnpm, they finish inside any
    # whole-second limit, so only a test that waits makes the timeout deterministic.
    slow_fix = dataclasses.replace(fix, test_paths=(SLOW,))
    with container.LOCK:
        slow = container.run_tests(slow_fix, {SLOW: SLOW_SPEC}, timeout=2)
    assert slow.timed_out is True and slow.status == "timeout"
    after = verify.verify(fix, [])
    assert after.status == "test_fail" and not after.timed_out, after.tail
    assert "Test Files" in after.tail, after.tail


def test_a_missing_base_is_infra_not_a_test_failure(fix):
    ghost = fixes.Fix(0, fix.fix_sha, fix.prefix_sha, fix.gold_tests, fix.test_paths, (), ())
    with container.LOCK:
        run = container.run_tests(ghost, _gold(fix))
    assert run.status == "infra" and run.exit_code != 0, run.tail


def test_discard_is_idempotent_and_prepare_rebuilds(fix):
    container.discard(fix)
    container.discard(fix)  # nothing left: still fine
    with container.LOCK:
        gone = container.run_tests(fix, _gold(fix))
    assert gone.status == "infra"  # base not prepared
    ok, tail = container.prepare(fix)
    assert ok, tail
    assert verify.verify(fix, []).status == "test_fail"


def test_the_test_container_sees_only_its_base_as_uid_1000(fix):
    name = container._name(fix, "probe")
    r = container._docker(*container._test_args(fix, name), "sh", "-c",
                          "id -u; ls /work; test ! -e /store && test ! -e /repo && echo isolated; "
                          f"touch /work/{fix.issue}/.probe && rm /work/{fix.issue}/.probe && echo writable")
    container._docker("rm", "-f", name, timeout=60)
    out = container._text(r)
    assert out.split()[:2] == ["1000", str(fix.issue)], out
    assert "isolated" in out and "writable" in out, out
