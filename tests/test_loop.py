"""The hand-written loop, driven by a scripted FakeClient. No API call anywhere."""
from __future__ import annotations

from types import SimpleNamespace as NS

import anthropic
import pytest

from agent import loop, tools
from api import schema as S
from retrieval.search import FileHit, Hit

CSS = "packages/vite/src/node/plugins/css.ts"
CUTOFF = "2024-01-01T00:00:00Z"


def _grounded(files):
    return S.GroundedTriageResult(
        reasoning="r", issue_type="bug", severity="medium",
        suspected_components=[], reproduction_steps=[], confidence=0.5,
        suspected_files=files,
    )


def _hit(path=CSS):
    return FileHit(path, 0.8, Hit(path, 0.8, 1, 40, "code"))


def _resp(content, stop="tool_use"):
    usage = NS(input_tokens=100, output_tokens=10,
               cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return NS(content=content, stop_reason=stop, usage=usage,
              model="claude-haiku-5-5", _request_id="req_x")


def _tool(id_, name, **inp):
    return NS(type="tool_use", id=id_, name=name, input=inp)


def _final(files):
    return _resp([NS(type="text", text=_grounded(files).model_dump_json())], "end_turn")


class FakeClient:
    def __init__(self, *responses):
        self.calls, self._responses = [], list(responses)
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return self._responses.pop(0)


def _run(*responses, **kw):
    client = FakeClient(*responses)
    return client, loop.run("t", "b", [_hit()], CUTOFF, client=client, **kw)


def test_end_turn_returns_a_validated_result():
    _, run = _run(_final([CSS]))
    assert run.result.suspected_files == [CSS]
    assert len(run.turns) == 1 and run.trace == []


def test_assistant_turn_is_appended_whole_including_thinking():
    content = [NS(type="thinking", thinking="hm", signature="s"),
               _tool("t1", "list_dir", path="packages")]
    _, run = _run(_resp(content), _final([CSS]))
    assert run.messages[1]["role"] == "assistant"
    assert run.messages[1]["content"] is content


def test_each_tool_use_gets_one_matching_result_in_one_message():
    content = [_tool("t1", "list_dir", path="packages"),
               _tool("t2", "list_dir", path="docs")]
    _, run = _run(_resp(content), _final([CSS]))
    msg = run.messages[2]
    assert msg["role"] == "user"
    assert [r["tool_use_id"] for r in msg["content"]] == ["t1", "t2"]
    assert all(r["type"] == "tool_result" for r in msg["content"])
    assert len(run.trace) == 2 and run.trace[0].turn == 1


def test_tool_error_is_sent_back_flagged():
    _, run = _run(_resp([_tool("t1", "read_file", path="nope.ts")]), _final([CSS]))
    assert run.messages[2]["content"][0]["is_error"] is True
    assert run.trace[0].is_error is True


def test_every_request_carries_tools_and_the_output_schema():
    client, _ = _run(_resp([_tool("t1", "list_dir", path="packages")]), _final([CSS]))
    assert len(client.calls) == 2
    for kw in client.calls:
        assert kw["tools"] == tools.TOOL_SPECS
        schema = kw["output_config"]["format"]["schema"]
        assert schema == anthropic.transform_schema(S.GroundedTriageResult)
        assert "'maxItems'" not in str(schema)  # as a key; the SDK moves it into a description


def test_turn_limit_raises_with_the_partial_run():
    t = lambda: _resp([_tool("t1", "list_dir", path="packages")])
    with pytest.raises(loop.RunError) as err:
        _run(t(), t(), t(), max_turns=2)
    assert len(err.value.run.turns) == 2
    assert err.value.run.result is None


def test_out_of_scope_answer_is_rejected():
    with pytest.raises(loop.RunError):
        _run(_final(["packages/vite/src/node/nope.ts"]))


def test_unparseable_final_text_is_a_run_error():
    with pytest.raises(loop.RunError):
        _run(_resp([NS(type="text", text="not json")], "end_turn"))


def test_max_tokens_is_a_run_error():
    with pytest.raises(loop.RunError):
        _run(_resp([NS(type="text", text="{")], "max_tokens"))


def test_closing_assistant_turn_is_in_messages():
    final = _final([CSS])
    _, run = _run(_resp([_tool("t1", "list_dir", path="packages")]), final)
    assert len(run.messages) == 4
    assert run.messages[3] == {"role": "assistant", "content": final.content}


def test_rejected_final_answer_is_still_in_the_runerror_messages():
    final = _final(["packages/vite/src/node/nope.ts"])
    with pytest.raises(loop.RunError) as err:
        _run(final)
    assert err.value.run.messages[-1] == {"role": "assistant", "content": final.content}


class _ApiError(anthropic.APIError):
    def __init__(self):
        super().__init__("overloaded", request=None, body=None)


class _FailingSecondClient(FakeClient):
    def create(self, **kw):
        if self.calls:
            raise _ApiError()
        return super().create(**kw)


def test_api_error_on_a_later_turn_keeps_the_partial_run():
    client = _FailingSecondClient(_resp([_tool("t1", "list_dir", path="packages")]))
    with pytest.raises(loop.RunError, match="API error on turn 2") as ei:
        loop.run("t", "b", [_hit()], CUTOFF, client=client)
    assert len(ei.value.run.turns) == 1
    assert isinstance(ei.value.__cause__, anthropic.APIError)


def test_tool_use_stop_without_tool_use_blocks_is_a_run_error():
    with pytest.raises(loop.RunError, match="no tool_use blocks") as ei:
        _run(_resp([NS(type="text", text="hm")]))
    assert len(ei.value.run.turns) == 1
