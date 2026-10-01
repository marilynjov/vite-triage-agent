"""Local models via Ollama. Free to run, and it costs you seconds instead.

Reference implementation: this one is finished. The Anthropic provider next to it
has the same shape with the call left for you to write — read this when you get
stuck on that one.

Ollama's token accounting uses different names than Anthropic's:
    prompt_eval_count  -> input tokens
    eval_count         -> output tokens
    done_reason        -> why it stopped ("stop", "length", ...)
There are no cache fields, because there is no server-side cache to read from.
"""
from __future__ import annotations

from ollama import Client

from .. import config as C
from .base import Completion

_client = Client(host=C.OLLAMA_HOST)


def _get(response, key: str, default=None):
    """Ollama returns an object in recent versions and a dict in older ones."""
    if isinstance(response, dict):
        return response.get(key, default)
    return getattr(response, key, default)


def complete(system: str, user: str, *, max_tokens: int | None = None) -> Completion:
    options = {"num_predict": max_tokens or C.MAX_TOKENS}

    response = _client.chat(
        model=C.OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        options=options,
    )

    message = _get(response, "message") or {}
    text = _get(message, "content") or ""

    return Completion(
        text=text,
        model=C.OLLAMA_MODEL,
        provider="ollama",
        input_tokens=_get(response, "prompt_eval_count", 0) or 0,
        output_tokens=_get(response, "eval_count", 0) or 0,
        stop_reason=_get(response, "done_reason"),
        raw=response,
    )
