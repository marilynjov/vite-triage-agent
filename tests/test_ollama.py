"""The local provider: what it actually sends to Ollama."""
from __future__ import annotations

from api.providers import ollama_provider


def test_ollama_sets_a_context_window_big_enough_for_candidates():
    # Ollama's default window silently drops the start of a long prompt —
    # the system prompt goes first.
    assert ollama_provider._options(100)["num_ctx"] >= 8192
