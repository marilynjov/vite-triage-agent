"""Patch contract: the edit models, the stage check against the pre-fix tree, and the diff."""
from __future__ import annotations

import subprocess
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

import mining.config as M
from agent import patch, stages, tools
from agent.loop import Budget, RunError
from api import schema as S

CSS = "packages/vite/src/node/plugins/css.ts"
OLD = tools._git("rev-parse", "72f6a52^").stdout.strip()


def _lines() -> list[str]:
    return tools._git("show", f"{OLD}:{CSS}").stdout.split("\n")


def _line(n: int) -> str:
    return _lines()[n - 1]


def _result(*edits: S.Edit) -> S.PatchResult:
    return S.PatchResult(reasoning="r", edits=list(edits))


def _edit(old: str, new: str = "x", path: str = CSS) -> S.Edit:
    return S.Edit(path=path, old=old, new=new)


def _real_line() -> str:
    return next(l for l in _lines() if l.strip() and l.count("import ") == 0 and len(l) > 20
                and _lines().count(l) == 1 and "\n" not in l)


def test_check_patch_accepts_a_real_edit():
    line = _real_line()
    stages.check_patch(OLD)(_result(_edit(line, line + " // note")))


def test_check_patch_rejects_path_absent():
    e = _edit("a", path="packages/vite/src/node/plugins/rolldownDepPlugin.ts")
    with pytest.raises(ValueError, match="no such file"):
        stages.check_patch(OLD)(_result(e))


def test_check_patch_rejects_outside_src():
    e = _edit('"name"', path="packages/vite/package.json")
    with pytest.raises(ValueError, match="not an editable source file"):
        stages.check_patch(OLD)(_result(e))


def test_check_patch_rejects_test_file():
    e = _edit("import", path="packages/vite/src/node/__tests__/plugins/css.spec.ts")
    with pytest.raises(ValueError, match="not an editable source file"):
        stages.check_patch(OLD)(_result(e))


def test_check_patch_rejects_old_missing():
    with pytest.raises(ValueError, match="occurs 0 times"):
        stages.check_patch(OLD)(_result(_edit("this text is not in css.ts at all 9f3a")))


def test_check_patch_rejects_old_repeated():
    n = "\n".join(_lines()).count("import ")
    assert n >= 2
    with pytest.raises(ValueError, match=f"occurs {n} times"):
        stages.check_patch(OLD)(_result(_edit("import ")))


def test_check_patch_rejects_overlapping_edits():
    text = "\n".join(_lines())
    ls = _lines()
    i = next(i for i in range(len(ls) - 1)
             if len(ls[i].strip()) > 15 and len(ls[i + 1].strip()) > 15
             and text.count(ls[i] + "\n" + ls[i + 1]) == 1 and text.count(ls[i + 1]) == 1)
    a, b = ls[i], ls[i + 1]
    first = _edit(a + "\n" + b)
    second = _edit(b)
    with pytest.raises(ValueError, match="overlap"):
        stages.check_patch(OLD)(_result(first, second))


def test_check_patch_rejects_line_number_prefix():
    line = _real_line()
    n = _lines().index(line) + 1
    stages.check_patch(OLD)(_result(_edit(line)))  # the bare line is fine
    with pytest.raises(ValueError, match="occurs 0 times"):
        stages.check_patch(OLD)(_result(_edit(f"{n}: " + line)))


def test_patch_result_caps_edits_at_5():
    # Like the other stage results the cap is in the JSON schema, not the model; the check
    # enforces it (it cannot truncate: dropping an edit would change the patch).
    assert S.json_schema(S.PatchResult)["properties"]["edits"]["maxItems"] == 5
    with pytest.raises(ValidationError):
        _result()
    line = _real_line()
    six = _result(*[_edit(line, "x")] * 6)
    with pytest.raises(ValueError, match="patch: .*at most 5"):
        stages.check_patch(OLD)(six)


def test_patch_to_diff_applies_at_rev(tmp_path):
    line = _real_line()
    diff = patch.patch_to_diff([_edit(line, line + " // note")], OLD)
    assert diff.startswith(f"--- a/{CSS}\n+++ b/{CSS}\n")
    archive = subprocess.run(["git", "-C", str(M.REPO_PATH), "archive", OLD, CSS],
                             capture_output=True, check=True).stdout
    subprocess.run(["tar", "-x", "-C", str(tmp_path)], input=archive, check=True)
    (tmp_path / "p.diff").write_text(diff)
    r = subprocess.run(["git", "apply", "--check", "p.diff"], cwd=tmp_path,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_apply_edits_returns_full_new_content():
    line = _real_line()
    out = patch.apply_edits([_edit(line, "REPLACED")], OLD)
    assert "REPLACED" in out[CSS] and line not in out[CSS]


def test_patch_prompt_says_lines_are_from_newer_code():
    hyp = {"fault": {"path": CSS, "start_line": 5, "end_line": 9, "mechanism": "m-unique"}}
    p = stages.patch_prompt("T", "B", hyp)
    assert "line numbers come from newer code" in p
    assert '"m-unique"' in p and "Title: T" in p


def test_patch_tools_are_read_only_subset():
    assert {t["name"] for t in stages.PATCH_TOOLS} == {"read_file", "grep", "list_dir"}


# --- run_patch: one loop call at the pre-fix commit ---

BIG = Budget(max_turns=99, max_tokens=10**9, max_seconds=10**6, max_dollars=100.0)
CUTOFF = "2024-01-01T00:00:00Z"
HYP = {"fault": {"path": CSS, "start_line": 1, "end_line": 5, "mechanism": "m"}}


def _resp(content, stop="end_turn"):
    usage = NS(input_tokens=100, output_tokens=10,
               cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return NS(content=content, stop_reason=stop, usage=usage,
              model="claude-haiku-5-5", _request_id="req_x")


def _tool_turn(path="nope.ts"):
    return _resp([NS(type="tool_use", id="t", name="read_file", input={"path": path})],
                 "tool_use")


class FakeClient:
    def __init__(self, *responses):
        self.calls, self._responses = [], list(responses)
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return self._responses.pop(0)


def _final(*edits):
    return _resp([NS(type="text", text=_result(*edits).model_dump_json())])


def _run(client):
    return patch.run_patch("t", "b", HYP, OLD, CUTOFF, budget=BIG, client=client)


def test_run_patch_sends_only_its_three_tools():
    line = _real_line()
    c = FakeClient(_final(_edit(line, line + " // note")))
    r = _run(c)
    assert {t["name"] for t in c.calls[0]["tools"]} == {"read_file", "grep", "list_dir"}
    assert r.result.edits[0].path == CSS


def test_run_patch_tools_read_the_prefix():
    gone = "packages/vite/src/node/plugins/rolldownDepPlugin.ts"
    line = _real_line()
    c = FakeClient(_tool_turn(gone), _final(_edit(line, line + " // note")))
    _run(c)
    results = c.calls[1]["messages"][2]["content"]  # shared list: index, not [-1]
    assert "no such file" in str(results[0]["content"])


def test_run_patch_caps_turns_at_6():
    assert patch.PATCH_TURNS == 6
    c = FakeClient(*[_tool_turn() for _ in range(10)])
    with pytest.raises(RunError):
        _run(c)
    assert len(c.calls) == 6


def test_run_patch_bad_edit_raises_run_error():
    c = FakeClient(_final(_edit("this text is not in the file at all zzz")))
    with pytest.raises(RunError, match="patch: "):
        _run(c)
