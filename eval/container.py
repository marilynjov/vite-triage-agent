"""The Docker side of verify: one image, a prepared base per issue, network-off test runs.

Volumes: `vtr-work` holds a base per issue at /work/<issue>; `vtr-store` holds the
pnpm store and corepack's cache, shared by every base. The Vite clone is mounted
read-only at /repo. `prepare` exports the pre-fix tree, commits it to a fresh git
repo (so it can be reset), installs once and bundles packages/vite (vitest loads
the workspace's vite from its dist, as Vite's own CI builds before `test-unit`). `run_tests` resets the base, lays the
given files over it and runs vitest on the fix's tests. Resetting at the start, not
the end, is what keeps a crashed or killed run from leaking into the next one.
Callers hold LOCK around `run_tests`: runs on one base must not overlap.
"""
from __future__ import annotations

import io
import shlex
import subprocess
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass

import mining.config as M
from eval.fixes import Fix

IMAGE = "vite-triage-verify:node18"
DOCKERFILE_DIR = M.ROOT / "eval" / "docker"
WORK_VOLUME = "vtr-work"
STORE_VOLUME = "vtr-store"
LOCK = threading.Lock()

BUILD_TIMEOUT = 1800
PREPARE_TIMEOUT = 3600
KILL_GRACE = 10  # seconds `timeout` waits after TERM before KILL
DOCKER_SLACK = 120  # container start/stop on top of the in-container timeout
TAIL_CHARS = 4000


@dataclass(frozen=True)
class TestRun:
    __test__ = False  # not a pytest class
    exit_code: int
    seconds: float
    tail: str
    timed_out: bool


def _docker(*args: str, input: bytes | None = None, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], input=input, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=False, timeout=timeout)


def _text(r: subprocess.CompletedProcess) -> str:
    return (r.stdout or b"").decode("utf-8", errors="replace")


def _tail(text: str) -> str:
    return text[-TAIL_CHARS:]


def ensure_image() -> None:
    if _docker("image", "inspect", IMAGE).returncode == 0:
        return
    r = _docker("build", "-t", IMAGE, str(DOCKERFILE_DIR), timeout=BUILD_TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"docker build {IMAGE} failed:\n{_tail(_text(r))}")


def _run_args(name: str, *extra: str) -> list[str]:
    return ["run", "--rm", "--name", name,
            "-v", f"{WORK_VOLUME}:/work", "-v", f"{STORE_VOLUME}:/store",
            "-v", f"{M.REPO_PATH}:/repo:ro",
            "-e", "COREPACK_HOME=/store/corepack", "-e", "COREPACK_ENABLE_DOWNLOAD_PROMPT=0",
            "-e", "CI=1", "-e", "NO_COLOR=1", *extra, IMAGE]


def _name(fix: Fix, what: str) -> str:
    return f"vtr-{what}-{fix.issue}-{uuid.uuid4().hex[:8]}"


def _kill(name: str) -> None:
    _docker("kill", name, timeout=60)


def prepare(fix: Fix) -> tuple[bool, str]:
    """Build /work/<issue> at the pre-fix commit and install it, unless already ready."""
    d = f"/work/{fix.issue}"
    script = f"""
set -e
d={d}
if [ -f "$d/.ready" ]; then echo "base ready: $d"; exit 0; fi
rm -rf "$d"
if ! (
  set -e
  mkdir -p "$d" && cd "$d"
  git -C /repo archive {shlex.quote(fix.prefix_sha)} | tar -x
  git init -q
  echo .ready >> .git/info/exclude
  git add -A
  git -c user.name=verify -c user.email=verify@localhost commit -qm base --no-verify
  pnpm --version
  pnpm install --frozen-lockfile --store-dir /store/pnpm
  pnpm --filter ./packages/vite run build-bundle
); then
  rm -rf "$d"
  echo "prepare failed: removed $d"
  exit 1
fi
touch "$d/.ready"
echo "base ready: $d"
"""
    name = _name(fix, "prepare")
    try:
        r = _docker(*_run_args(name), "sh", "-c", script, timeout=PREPARE_TIMEOUT)
    except subprocess.TimeoutExpired:
        _kill(name)
        return False, f"prepare timed out after {PREPARE_TIMEOUT}s"
    return r.returncode == 0, _tail(_text(r))


def _tar(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for path, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(data), 0o644, int(time.time())
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def run_tests(fix: Fix, files: dict[str, str], delete: tuple[str, ...] = (),
              timeout: int = 300) -> TestRun:
    """Reset the base, delete `delete`, write `files`, run vitest on the fix's tests offline."""
    rm = " ".join(shlex.quote(p) for p in delete)
    tests = " ".join(shlex.quote(p) for p in fix.test_paths)
    script = f"""
set -e
cd /work/{fix.issue}
[ -f .ready ] || {{ echo "base not prepared"; exit 125; }}
git reset --hard -q
git clean -fdq
{f"rm -rf -- {rm}" if rm else ":"}
tar -x
set +e
timeout -k {KILL_GRACE} {int(timeout)} pnpm exec vitest run {tests}
"""
    name = _name(fix, "test")
    args = _run_args(name, "-i", "--network", "none", "--cpus", "4", "--memory", "4g")
    start = time.monotonic()
    try:
        r = _docker(*args, "sh", "-c", script, input=_tar(files),
                    timeout=timeout + KILL_GRACE + DOCKER_SLACK)
    except subprocess.TimeoutExpired as e:
        _kill(name)
        out = (e.stdout or b"").decode("utf-8", errors="replace")
        return TestRun(-1, time.monotonic() - start, _tail(out), True)
    seconds = time.monotonic() - start
    # 124: `timeout` sent TERM; 137 after the full wait: it had to KILL.
    timed_out = r.returncode == 124 or (r.returncode == 137 and seconds >= timeout)
    return TestRun(r.returncode, seconds, _tail(_text(r)), timed_out)
