"""One normalized result shape, whatever answered the question.

Every provider returns this. Nothing downstream — the route, the cost
calculation, the calls table, the eval harness — should ever need to know which
one it was talking to, except to price it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Completion:
    text: str
    model: str
    provider: str

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    stop_reason: str | None = None
    request_id: str | None = None

    # Phase 02: the validated TriageResult, or None for a prose call. Typed as
    # Any so this module stays free of a schema import — providers never need to
    # know what shape they were asked for, only to validate against it.
    parsed: Any = None

    # The untouched provider response, for when a number looks wrong and you
    # need to see what actually came back.
    raw: Any = field(default=None, repr=False)

    @property
    def priced(self) -> bool:
        """False for anything running on your own hardware."""
        return self.provider == "anthropic"
