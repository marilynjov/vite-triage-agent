"""Provider selection. One switch, resolved once at import.

Deliberately not a registry or a plugin system: there are two providers and
there will not be twelve. `PROVIDER=ollama` in .env costs nothing to run;
`PROVIDER=anthropic` costs money and is what the gate numbers are quoted from.
"""
from __future__ import annotations

from .. import config as C
from .base import Completion


def complete(system: str, user: str, *, max_tokens: int | None = None) -> Completion:
    """Send one prompt, get one Completion, whichever provider is configured."""
    if C.PROVIDER == "anthropic":
        from . import anthropic_provider
        return anthropic_provider.complete(system, user, max_tokens=max_tokens)
    if C.PROVIDER == "ollama":
        from . import ollama_provider
        return ollama_provider.complete(system, user, max_tokens=max_tokens)
    raise ValueError(
        f"unknown PROVIDER {C.PROVIDER!r} — expected 'ollama' or 'anthropic'"
    )


def complete_structured(
    system: str,
    user: str,
    model: type,
    *,
    max_tokens: int | None = None,
) -> Completion:
    """Same dispatch, but the answer is constrained to `model`'s JSON Schema.

    `model` is a Pydantic class. The returned Completion carries the validated
    instance on `.parsed`, so no caller downstream ever parses JSON or guards a
    missing field.
    """
    if C.PROVIDER == "anthropic":
        from . import anthropic_provider
        return anthropic_provider.complete_structured(system, user, model, max_tokens=max_tokens)
    if C.PROVIDER == "ollama":
        from . import ollama_provider
        return ollama_provider.complete_structured(system, user, model, max_tokens=max_tokens)
    raise ValueError(
        f"unknown PROVIDER {C.PROVIDER!r} — expected 'ollama' or 'anthropic'"
    )


__all__ = ["Completion", "complete", "complete_structured"]
