"""The local provider: what it actually sends to Ollama."""
from __future__ import annotations

from api import schema as S
from api.providers import ollama_provider


def test_ollama_sets_a_context_window_big_enough_for_candidates():
    # Ollama's default window silently drops the start of a long prompt —
    # the system prompt goes first.
    assert ollama_provider._options(100)["num_ctx"] >= 8192


def test_ollama_sends_the_schema_of_the_model_it_was_asked_for(monkeypatch):
    sent = {}
    result = S.GroundedTriageResult(
        reasoning="r", issue_type="bug", severity="medium",
        suspected_components=[], reproduction_steps=[], confidence=0.5,
        suspected_files=["a.ts"],
    )

    def fake_chat(**kwargs):
        sent.update(kwargs)
        return {"message": {"content": result.model_dump_json()}}

    monkeypatch.setattr(ollama_provider._client, "chat", fake_chat)
    ollama_provider.complete_structured("s", "u", S.GroundedTriageResult)
    assert "suspected_files" in sent["format"]["properties"]
