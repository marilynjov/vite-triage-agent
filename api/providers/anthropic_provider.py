"""The Claude API. Costs money; produces the numbers your gates are quoted from.

Three things worth knowing before you write the call below:

  * Opus 5 thinks by default. Omitting `thinking` runs adaptive thinking, and
    those tokens bill as output while `display` defaults to omitted — so you pay
    for reasoning you never see. Control the depth with
    `output_config={"effort": "low"|"medium"|"high"}`. Do NOT pass
    `thinking={"type": "disabled"}` to save money: on Opus 5 that can leak
    reasoning into the visible text and sometimes writes tool calls as prose.
  * `response.content` is a list of blocks, not a string. A thinking block has no
    `.text`. Match on `block.type == "text"` — never `content[0].text`.
  * Check `response.stop_reason` before trusting the text. `max_tokens` means it
    was cut off mid-thought; `refusal` means there is no answer in there at all.
"""
from __future__ import annotations

import os

import anthropic

from .. import config as C
from .base import Completion

KEY_HELP = """
  Create one at https://console.anthropic.com/settings/keys
    "Create Key" — it is shown once, so copy it immediately.
  Then replace the placeholder in .env:
    ANTHROPIC_API_KEY=sk-ant-api03-...
  Nothing here needs a key while PROVIDER=ollama.
"""


def _client() -> anthropic.Anthropic:
    """Fail with a sentence, not a 401, when the key was never pasted in.

    Same guard as mining/gh.py applies to the GitHub token: the placeholder from
    .env.example is the single most likely first-run mistake.
    """
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise SystemExit("no ANTHROPIC_API_KEY in .env\n" + KEY_HELP)
    if not key.startswith("sk-ant-") or len(key) < 50:
        raise SystemExit(
            "ANTHROPIC_API_KEY in .env is still the placeholder from "
            ".env.example.\n" + KEY_HELP
        )
    return anthropic.Anthropic(api_key=key)


def complete(system: str, user: str, *, max_tokens: int | None = None) -> Completion:
    """
    One call to Claude, normalized into a Completion.
    """

    response = _client().messages.create(
      model=C.ANTHROPIC_MODEL,
      max_tokens=max_tokens or C.MAX_TOKENS,
      system=system,
      messages=[{"role": "user", "content": user}],
    )

    prose = next(
        (block.text for block in response.content if block.type == "text"),""
    )
        
    input_tokens = getattr(response.usage, "input_tokens", 0) or 0
    output_tokens = getattr(response.usage, "output_tokens", 0) or 0
    cache_creation_input_tokens = getattr(response.usage, "cache_creation_input_tokens", 0  ) or 0
    cache_read_input_tokens = getattr(response.usage, "cache_read_input_tokens", 0) or 0

    return Completion(
        text=prose,
        model=C.ANTHROPIC_MODEL,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,          
        stop_reason=response.stop_reason,
        request_id=response._request_id,
        provider="anthropic",
        raw=response,
    )


def complete_structured(
    system: str,
    user: str,
    model: type,
    *,
    max_tokens: int | None = None,
) -> Completion:
    """Schema-constrained paid call.

    `messages.parse(output_format=<pydantic class>)` is the SDK helper — not the
    deprecated top-level `output_format` request parameter. It validates for you
    and hands back the instance on `.parsed_output`. The raw wire equivalent is
    `output_config={"format": {"type": "json_schema", "schema": {...}}}` on
    messages.create(), which you only need if you want the unvalidated JSON.
    """
    response = _client().messages.parse(
        model=C.ANTHROPIC_MODEL,
        max_tokens=max_tokens or C.MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=model,
    )

    # Before trusting parsed_output. `max_tokens` means a truncated object;
    # `refusal` means there is no object at all and stop_details says why.
    if response.stop_reason == "max_tokens":
        raise ValueError(
            f"truncated at max_tokens ({response.usage.output_tokens} out) — "
            "the object is incomplete; raise MAX_TOKENS"
        )
    if response.stop_reason == "refusal":
        category = getattr(response.stop_details, "category", None)
        raise ValueError(f"model refused (category={category})")

    usage = response.usage
    return Completion(
        text=next((b.text for b in response.content if b.type == "text"), ""),
        model=response.model,
        provider="anthropic",
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        stop_reason=response.stop_reason,
        request_id=response._request_id,
        parsed=response.parsed_output,
        raw=response,
    )
