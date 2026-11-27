"""inbox.sync: follow the default branch of a real (temporary) clone; re-pin on request."""
import subprocess

from inbox import sync


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(cwd, name):
    (cwd / name).write_text(name)
    git("add", name, cwd=cwd)
    git("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false",
        "commit", "-qm", name, cwd=cwd)
    return git("rev-parse", "HEAD", cwd=cwd)


def make(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", "-b", "main", cwd=origin)
    commit(origin, "a")
    git("clone", "-q", str(origin), str(tmp_path / "clone"), cwd=tmp_path)
    return origin, tmp_path / "clone"


def test_branch_head_sees_a_new_remote_commit(tmp_path):
    origin, clone = make(tmp_path)
    new = commit(origin, "b")
    assert sync.branch_head(clone, "main") == new


def test_default_branch_from_origin_head_else_main(tmp_path):
    _, clone = make(tmp_path)
    assert sync.default_branch(clone) == "main"
    git("remote", "set-head", "origin", "-d", cwd=clone)
    assert sync.default_branch(clone) == "main"


def test_repin_writes_the_pin_then_reindexes(tmp_path):
    pin = tmp_path / "pinned_sha.txt"
    pin.write_text("old\n")
    seen = []
    sync.repin("new123", pin_path=pin, reindex=lambda: seen.append(pin.read_text()))
    assert seen == ["new123\n"]
