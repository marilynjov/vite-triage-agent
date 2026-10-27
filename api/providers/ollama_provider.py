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


def _options(max_tokens: int | None) -> dict:
    """num_ctx is not optional once retrieval feeds the prompt.

    Ollama's default context window is a few thousand tokens, and a prompt
    longer than that is truncated from the FRONT, silently — the system prompt
    is the first thing lost. A grounded triage is ~5k tokens, so without this
    the local model would score badly for a reason unrelated to the model.
    """
    return {"num_predict": max_tokens or C.MAX_TOKENS, "num_ctx": C.OLLAMA_NUM_CTX}


def complete(system: str, user: str, *, max_tokens: int | None = None) -> Completion:
    response = _client.chat(
        model=C.OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        options=_options(max_tokens),
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


def complete_structured(
    system: str,
    user: str,
    model: type,
    *,
    max_tokens: int | None = None,
) -> Completion:
    """Schema-constrained local call. Finished — read it for the paid one.

    Ollama takes a JSON Schema directly in `format`, and llama.cpp turns it into
    a grammar, so the structure is guaranteed rather than requested. Two things
    it does NOT guarantee, both measured:

      * numeric ranges. `maximum: 1` does not stop a model returning 90.
      * length. Without `maxItems` an array rambles until max_tokens cuts the
        JSON off mid-string.

    So the schema is necessary and not sufficient: `model.model_validate` below
    is what actually makes the result safe, and it is the reason the frontend
    needs no defensive code.
    """
    from .. import schema as S

    response = _client.chat(
        model=C.OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        format=S.json_schema(),
        # Structured output is longer than prose; a truncated object is useless
        # where truncated prose is merely short.
        options=_options(max_tokens),
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
        parsed=model.model_validate_json(text),
        raw=response,
    )
