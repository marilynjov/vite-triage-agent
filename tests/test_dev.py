"""scripts/dev.py: the preflight checks and the two-process supervisor, with fakes."""
import importlib.util
import json
import subprocess

import mining.config as M

spec = importlib.util.spec_from_file_location("dev", M.ROOT / "scripts" / "dev.py")
dev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dev)

TAGS = json.dumps({"models": [{"name": "nomic-embed-text:latest"}, {"name": "qwen2.5-coder:7b"}]})


def ready_root(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    (tmp_path / "web" / "node_modules").mkdir(parents=True)
    return tmp_path


def check(root, *, docker_rc=0, docker_path="/usr/bin/docker", tags=TAGS, busy=()):
    def http_get(url):
        if isinstance(tags, Exception):
            raise tags
        return tags
    return dev.preflight(
        root=root, which=lambda name: docker_path,
        run=lambda args, **k: subprocess.CompletedProcess(args, docker_rc, b"", b""),
        http_get=http_get, port_free=lambda port: port not in busy)


def test_all_ready_is_no_problems(tmp_path):
    assert check(ready_root(tmp_path)) == []


def test_each_problem_names_its_fix(tmp_path):
    problems = check(tmp_path, docker_rc=1, tags=OSError("refused"), busy=(8000, 5173))
    joined = "\n".join(problems)
    assert len(problems) == 6
    for fix in ("python3 -m venv .venv", "npm --prefix web ci", "open -a Docker",
                "ollama serve", "lsof -i :8000", "lsof -i :5173"):
        assert fix in joined


def test_no_docker_binary_and_a_missing_embedding_model(tmp_path):
    problems = check(ready_root(tmp_path), docker_path=None,
                     tags=json.dumps({"models": [{"name": "qwen2.5-coder:7b"}]}))
    assert len(problems) == 2
    assert "Docker" in problems[0] and "ollama pull nomic-embed-text" in problems[1]


class Proc:
    def __init__(self, codes):
        self.codes = list(codes)  # successive poll() results; the last one repeats
        self.terminated = self.killed = False

    def poll(self):
        if self.terminated:
            return -15
        return self.codes.pop(0) if len(self.codes) > 1 else self.codes[0]

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return -15

    def kill(self):
        self.killed = True


def test_when_one_process_exits_the_other_is_stopped_and_its_code_returned():
    api, web = Proc([None, None, 3]), Proc([None])
    assert dev.supervise([api, web], poll=0, sleep=lambda s: None) == 3
    assert web.terminated and not api.terminated


def test_ctrl_c_stops_both_and_returns_130():
    api, web = Proc([None]), Proc([None])

    def interrupt(seconds):
        raise KeyboardInterrupt

    assert dev.supervise([api, web], poll=0, sleep=interrupt) == 130
    assert api.terminated and web.terminated
