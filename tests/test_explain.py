from decimal import Decimal
from types import SimpleNamespace as NS

from agent.explain import format_transcript
from agent.loop import Turn


def _msgs():
    return [
        {"role": "user", "content": "issue text"},
        {"role": "assistant", "content": [
            NS(type="thinking", thinking="", signature="sig123"),
            NS(type="tool_use", id="toolu_1", name="read_file", input={"path": "a.ts"}),
            NS(type="tool_use", id="toolu_2", name="grep", input={"pattern": "x"}),
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "x", "is_error": False},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "boom", "is_error": True},
        ]},
        {"role": "assistant", "content": [NS(type="text", text="done")]},
    ]


def _turns():
    return [Turn(10, 5, 0, 0, "tool_use", "r1", 100, Decimal("0.001")),
            Turn(20, 7, 0, 0, "end_turn", "r2", 100, Decimal("0.002"))]


def test_transcript_pairs_parallel_tool_calls():
    out = format_transcript(_msgs(), _turns())
    lines = out.splitlines()
    assert "[1] assistant" in out
    assert "tool_use toolu_1 read_file" in out
    r1 = next(l for l in lines if l.strip().startswith("tool_result toolu_1"))
    r2 = next(l for l in lines if l.strip().startswith("tool_result toolu_2"))
    assert "read_file" in r1 and "answers [1]" in r1 and "is_error=False" in r1
    assert "grep" in r2 and "answers [1]" in r2 and "is_error=True" in r2
    assert out.index("tool_use toolu_1") < out.index("tool_result toolu_1")
    assert "text hidden by the API" in out and "signature 6 chars" in out
    assert "clipped to 60 chars" in out and "HOW TO READ THIS" in out


def test_token_lines_map_turns_to_assistant_messages():
    lines = format_transcript(_msgs(), _turns()).splitlines()
    i1 = lines.index("[1] assistant")
    i3 = lines.index("[3] assistant")
    between = "\n".join(lines[i1:lines.index("[2] user")])
    closing = "\n".join(lines[i3:])
    assert "API call 1: input=10 output=5" in between and "stop_reason=tool_use" in between
    assert "API call 2: input=20 output=7" in closing and "stop_reason=end_turn" in closing
