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


__all__ = ["Completion", "complete"]
