"""The naive baseline: issue text in, prose out. No retrieval, no tools.

Everything measurable in the next six phases is measured against whatever this
produces. Which model answers is a config question — see api/providers/.
"""
from __future__ import annotations

from . import config as C
from .providers import Completion, complete

SYSTEM = """\
You are standing in for a Vite maintainer doing first-pass triage on a bug
report.

Read the bug report and provide a concise triage assessment. Explain:

- what kind of issue it appears to be,
- how severe or impactful it looks,
- which parts of the codebase are probably involved,
- how confident you are in that assessment.

Keep the assessment grounded only in the information provided in the bug
report. Be explicit about uncertainty and do not pretend you have inspected
the codebase, reproduced the issue, or confirmed the root cause when you have
not.
"""

def triage_naive(title: str, body: str) -> Completion:
    """One call, whichever provider is configured.

    The prompt is the only thing this function decides. Token accounting,
    stop reasons and provider quirks are the provider's problem, and pricing is
    cost.py's — which is why this stayed three lines.
    """
    return complete(SYSTEM, f"Title: {title}\n\n{body}", max_tokens=C.MAX_TOKENS)
