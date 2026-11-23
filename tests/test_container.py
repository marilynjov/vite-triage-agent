"""The container's pure parts: the prepare script's failure path (run on the host with sh,
no Docker) and the run-status classification."""
import os
import subprocess

import pytest

import mining.config as M
from eval import container
from eval.fixes import Fix

PREFIX = "cf80e40e43d6d1c358a59be25daa3bd3a2c7c6c6"  # the commit before #14515's fix


ME = f"{os.getuid()}:{os.getgid()}"  # a non-root host can only chown to itself


def _fix(prefix=PREFIX):
    return Fix(1, "f" * 40, prefix, (), (), (), ())


def _run(tmp_path, fix, install, build):
    script = container._prepare_script(fix, work=str(tmp_path), repo=str(M.REPO_PATH),
                                       install=install, build=build, owner=ME)
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


def test_discard_removes_the_base_and_its_tar_offline_under_the_lock(monkeypatch):
    calls = []
    def fake(*args, input=None, timeout=120):
        calls.append((args, container.LOCK._is_owned()))
        return subprocess.CompletedProcess(args, 0, b"")
    monkeypatch.setattr(container, "_docker", fake)
    container.discard(_fix())
    (args, held), = calls
    assert held and args[0] == "run" and "--rm" in args
    assert args[args.index("--network") + 1] == "none"
    assert f"{container.WORK_VOLUME}:/work" in args
    assert list(args[-4:]) == ["rm", "-rf", "/work/1", "/work/1.tar"]


def test_discard_raises_on_docker_failure(monkeypatch):
    monkeypatch.setattr(container, "_docker",
                        lambda *a, **k: subprocess.CompletedProcess(a, 125, b"no daemon"))
    with pytest.raises(RuntimeError, match="no daemon"):
        container.discard(_fix())


def test_test_args_mount_only_the_issue_base_as_non_root():
    args = container._test_args(_fix(), "vtr-test-1-x")
    joined = " ".join(args)
    assert "type=volume,src=vtr-work,dst=/work/1,volume-subpath=1" in args
    assert "vtr-store" not in joined and ":/repo" not in joined and "COREPACK" not in joined
    for flag, value in [("--user", "1000:1000"), ("--pids-limit", "512"), ("--cap-drop", "ALL"),
                        ("--security-opt", "no-new-privileges"), ("--network", "none"),
                        ("--cpus", "4"), ("--memory", "4g")]:
        assert args[args.index(flag) + 1] == value
    assert args[-1] == container.IMAGE and "--rm" not in args


def test_prepare_hands_the_base_to_the_test_user_even_when_already_ready(tmp_path):
    script = container._prepare_script(_fix(), work=str(tmp_path), repo=str(M.REPO_PATH),
                                       install="true", build="true", owner=ME)
    assert script.count(f'chown -R {ME} "$d"') == 2  # ready-exit branch and before .ready
    first = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=300)
    assert first.returncode == 0, first.stdout + first.stderr
    again = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=300)
    assert again.returncode == 0 and "base ready" in again.stdout


def test_the_default_owner_is_the_test_user():
    assert 'chown -R 1000:1000 "$d"' in container._prepare_script(_fix())


def test_run_tests_calls_vitest_directly_not_through_corepack(monkeypatch):
    calls = []
    def fake(*args, input=None, timeout=120):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, b" Test Files  1 failed (1)\n")
    monkeypatch.setattr(container, "_docker", fake)
    container.run_tests(Fix(1, "f" * 40, PREFIX, (), ("a.spec.ts",), (), ()), {})
    script = next(a[-1] for a in calls if a[0] == "run")
    assert "./node_modules/.bin/vitest run a.spec.ts" in script and "pnpm exec" not in script
