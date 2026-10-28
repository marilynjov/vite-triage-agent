from decimal import Decimal
from types import SimpleNamespace as NS

from agent.explain import format_transcript
from agent.loop import Turn


def test_transcript_pairs_tool_use_with_result():
    messages = [
        {"role": "user", "content": "issue text"},
        {"role": "assistant", "content": [
            NS(type="thinking", thinking="hmm"),
            NS(type="tool_use", id="toolu_1", name="read_file", input={"path": "a.ts"}),
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "x", "is_error": False},
        ]},
    ]
    turns = [Turn(10, 5, 0, 0, "tool_use", "req_1", 100, Decimal("0.001"))]
    out = format_transcript(messages, turns)
    assert "[1] assistant" in out
    assert "tool_use toolu_1 read_file" in out
    assert "tool_result toolu_1" in out
    assert out.index("tool_use toolu_1") < out.index("tool_result toolu_1")
    assert "stop_reason=tool_use" in out
