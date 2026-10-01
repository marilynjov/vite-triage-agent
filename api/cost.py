"""What a call cost, in dollars, from the token counts the provider reports.

Prices are per million tokens. Claude Haiku 4.5 is $1 in / $5 out; Claude Opus 5
is $5 in / $25 out. Output is five times the price of input on every model, and
thinking tokens count as output — so on this project output dominates the bill,
not context size.

Four kinds of token bill at four different rates:

    input_tokens                    full input rate
    output_tokens                   the output rate (5x input; includes thinking)
    cache_creation_input_tokens     1.25x the input rate — writing costs a premium
    cache_read_input_tokens         0.1x the input rate — the point of caching

Today the two cache numbers are always zero. From Phase 07 they are not, which is
why they have a line here already.
"""
from __future__ import annotations

from decimal import Decimal

from .providers.base import Completion

PER_MTOK = {
    "claude-haiku-4-5": {"input": Decimal("1.00"), "output": Decimal("5.00")},
    "claude-sonnet-5": {"input": Decimal("2.00"), "output": Decimal("10.00")},
    "claude-opus-5": {"input": Decimal("5.00"), "output": Decimal("25.00")},
}

CACHE_WRITE_RATE = Decimal("1.25")   # x the input rate
CACHE_READ_RATE = Decimal("0.1")     # x the input rate
MILLION = Decimal("1000000")

ZERO = Decimal("0")


def compute_cost(completion: Completion) -> Decimal:
    """
    Dollar cost of one call. Zero for anything that ran on this machine.
    """
    if not completion.priced:
        return ZERO

    cost = completion.input_tokens/MILLION * PER_MTOK[completion.model]["input"]
    cost += completion.output_tokens/MILLION * PER_MTOK[completion.model]["output"]
    cost += completion.cache_creation_input_tokens/MILLION * PER_MTOK[completion.model]["input"] * CACHE_WRITE_RATE
    cost += completion.cache_read_input_tokens/MILLION * PER_MTOK[completion.model]["input"] * CACHE_READ_RATE
    return cost