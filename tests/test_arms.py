"""The ablation arms: one loop, with only the system prompt and tool list varying."""
from __future__ import annotations

from agent import loop
from agent.loop import AGENT_SYSTEM
from agent.tools import TOOL_SPECS
from api.triage import GROUNDED_SYSTEM
from eval.arms import ARMS, LOOK_FIRST, MODEL_ARMS

from tests.test_loop import CSS, CUTOFF, FakeClient, _final, _hit


def _run(*responses, **kw):
    client = FakeClient(*responses)
    return client, loop.run("t", "b", [_hit()], CUTOFF, client=client, **kw)


def test_arms_table_has_the_five_arms_with_the_right_prompt_and_tools():
    assert set(ARMS) == {"grounded", "agent-no-tools", "agent", "agent-look", "retrieval"}
    assert ARMS["grounded"].system == GROUNDED_SYSTEM and ARMS["grounded"].tool_specs == []
    assert ARMS["agent-no-tools"].system == AGENT_SYSTEM and ARMS["agent-no-tools"].tool_specs == []
    assert ARMS["agent"].system == AGENT_SYSTEM and ARMS["agent"].tool_specs == TOOL_SPECS
    assert ARMS["agent-look"].system == AGENT_SYSTEM + "\n\n" + LOOK_FIRST
    assert ARMS["agent-look"].tool_specs == TOOL_SPECS
    assert ARMS["retrieval"].system is None
    assert MODEL_ARMS == ("grounded", "agent-no-tools", "agent", "agent-look")


def test_look_first_is_the_spec_text():
    assert LOOK_FIRST.startswith("If your conclusion depends on code or documents")


def test_no_tools_request_omits_the_tools_key():
    client, _ = _run(_final([CSS]), tool_specs=[])
    assert "tools" not in client.calls[0]
    assert "output_config" in client.calls[0]


def test_no_tools_run_ends_after_one_turn():
    _, run = _run(_final([CSS]), tool_specs=[])
    assert len(run.turns) == 1
    assert run.result.suspected_files == [CSS]


def test_custom_system_is_sent():
    client, _ = _run(_final([CSS]), system="X")
    assert client.calls[0]["system"] == "X"


def test_defaults_are_unchanged():
    client, _ = _run(_final([CSS]))
    assert client.calls[0]["system"] == AGENT_SYSTEM
    assert client.calls[0]["tools"] == TOOL_SPECS
