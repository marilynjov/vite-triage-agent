"""The container's pure parts: the prepare script's failure path (run on the host with sh,
no Docker) and the run-status classification."""
import subprocess

import pytest

import mining.config as M
from eval import container
from eval.fixes import Fix

PREFIX = "cf80e40e43d6d1c358a59be25daa3bd3a2c7c6c6"  # the commit before #14515's fix


def _fix(prefix=PREFIX):
    return Fix(1, "f" * 40, prefix, (), (), (), ())


def _run(tmp_path, fix, install, build):
    script = container._prepare_script(fix, work=str(tmp_path), repo=str(M.REPO_PATH),
                                       install=install, build=build)
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=300)


@pytest.mark.parametrize("install,build", [("false", "true"), ("true", "false")])
def test_a_failed_step_leaves_no_base_even_if_later_steps_succeed(tmp_path, install, build):
    r = _run(tmp_path, _fix(), install, build)
    assert r.returncode != 0, r.stdout
    assert not (tmp_path / "1").exists()
    assert "prepare failed" in r.stdout


def test_a_failed_archive_leaves_no_base(tmp_path):
    r = _run(tmp_path, _fix("0" * 40), "true", "true")
    assert r.returncode != 0
    assert not (tmp_path / "1").exists()


def test_a_good_prepare_is_ready_clean_and_skipped_next_time(tmp_path):
    r = _run(tmp_path, _fix(), "true", "true")
    assert r.returncode == 0, r.stdout + r.stderr
    base = tmp_path / "1"
    assert (base / ".ready").exists() and (base / "packages/vite/package.json").exists()
    st = subprocess.run(["git", "-C", str(base), "status", "--porcelain"], capture_output=True, text=True)
    assert st.stdout == ""
    again = _run(tmp_path, _fix(), "false", "false")
    assert again.returncode == 0 and "base ready" in again.stdout


VITEST_FAIL = " Test Files  1 failed (1)\n      Tests  5 failed | 20 passed (25)\n"
VITEST_LOAD_FAIL = " Test Files  1 failed (1)\n      Tests  no tests\n"


@pytest.mark.parametrize("code,out,timed_out,oom,status", [
    (0, " Test Files  1 passed (1)\n", False, False, "pass"),
    (1, VITEST_FAIL, False, False, "test_fail"),
    (1, VITEST_LOAD_FAIL, False, False, "test_fail"),
    (124, "", True, False, "timeout"),
    (137, "", True, False, "timeout"),
    (137, VITEST_FAIL, False, True, "infra"),
    (1, "No test files found, exiting with code 1\n", False, False, "infra"),
    (125, "base not prepared", False, False, "infra"),
    (128, "fatal: could not reset", False, False, "infra"),
    (2, "tar: Unexpected EOF", False, False, "infra"),
    (1, "ERR_MODULE_NOT_FOUND", False, False, "infra"),
])
def test_status(code, out, timed_out, oom, status):
    assert container.classify(code, out, timed_out, oom) == status
