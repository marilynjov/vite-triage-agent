"""The naive baseline: issue text in, prose out. No retrieval, no tools.

Everything measurable in the next six phases is measured against whatever this
produces. Which model answers is a config question — see api/providers/.
"""
from __future__ import annotations

from . import config as C
from .providers import Completion, complete, complete_structured
from .schema import GroundedTriageResult, TriageResult

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


STRUCTURED_SYSTEM = """\
You are an issue triage assistant for the Vite repository. Analyze each issue
report in a single pass to determine its type, severity, affected components,
reproduction steps, and prediction confidence. Base every decision on evidence
explicitly present in the report. You have not read the code.

**Reasoning**

First, identify the evidence that supports your decisions. Describe the reported
behavior or requested change, the expected behavior if stated, the nature of the
problem, and any clues about its impact or affected areas. Missing information is
not evidence.

**Issue type**

Classify the report by its primary purpose:
- `bug`: existing functionality does not work as documented or intended.
- `regression`: behavior that previously worked has stopped working. Look for a
  version where it broke, or a statement that an upgrade caused it.
- `enhancement`: a request for functionality that does not exist yet.
- `documentation`: the docs are wrong, missing, or unclear; the code is fine.
- `question`: the reporter seeks information and asks for no change.
- `upstream`: the defect is in a dependency (Rollup, esbuild, a plugin, a
  browser), not in Vite itself.

Choose the category that represents the report's central concern. Unexpected
behavior is not automatically a bug — it may be a misunderstanding, a question,
or intended behavior the reporter disagrees with.

**Severity**

Severity here means maintainer priority: how soon would a maintainer schedule
this, not how annoyed the reporter is. Ask what release this would land in.

- `low`: an edge case, an uncommon configuration, a cosmetic problem, or a
  nice-to-have improvement. A maintainer would accept a fix from a volunteer but
  would not plan the work. The reporter may well be blocked and this is still
  low, if the situation that triggers it is unusual.
- `medium`: a genuine bug in ordinary use, or a problem blocking a downstream
  project, that has a workaround or a narrow blast radius. Scheduled in the
  normal course of work. This is the most common verdict.
- `high`: breaks a common workflow for many users with no reasonable workaround.
  A maintainer would want it in the next patch release.

Signals that move the verdict down: a stated workaround, an unusual
configuration, a specific integration, a single affected project, cosmetic-only
consequences. Signals that move it up: no workaround, the default configuration,
a broken build or dev server, a common framework or plugin.

Judge the reported impact rather than how dramatic the wording sounds. Do not
infer severity from emotional language or from the presence of words such as
"blocker" — something described as blocking one downstream project is `medium`.
Commit to the tier the signals point at. Do not retreat to `medium` as a hedge:
a verdict that is always `medium` carries no information, and `low` and `high`
each describe a third of real reports between them.

**Components**

Name the areas of this codebase the report implicates. The valid areas are:

  css, ssr, hmr, web workers, html, deps optimizer, library mode, build,
  sourcemap, dev server, wasm, environment API, preview, config, resolve,
  plugin system

Choose only areas the evidence supports, and prefer the most specific one. A
report may involve more than one. An empty list is the correct answer when the
affected area cannot be identified — do not force an assignment.

Never answer with a programming language, a framework, a package manager, an
operating system, an external tool, a generic description of software
development, or the name of the project itself.

**Reproduction steps**

List only reproduction steps explicitly stated in the report. Preserve their
meaning without adding missing actions, commands, configuration values, expected
results, or environmental details. If the report provides none, return an empty
list.

**Confidence**

Estimate confidence as a number between 0 and 1, based on the evidence available
in the report and the ambiguity of the classification:
- `0.90-1.00`: the report clearly identifies the issue or request and provides
  specific evidence that strongly supports the classification and components.
- `0.70-0.89`: the main issue is clear, but some details or a classification
  decision remain uncertain.
- `0.40-0.69`: the report is incomplete or ambiguous, with multiple plausible
  classifications or affected components.
- `0.00-0.39`: the report provides very little actionable information, and its
  intended meaning or classification is highly uncertain.

Consider evidence such as a reproduction link, a named error message, a version
associated with a regression, or a minimal reproduction. These are indicators,
not requirements: their absence does not imply low confidence if the report is
otherwise clear, and technical detail does not imply high confidence if the
underlying issue remains ambiguous. Do not inflate confidence because a report is
long, uses technical terminology, or sounds authoritative.

**Constraints**

- Never invent reproduction steps, error messages, version numbers, links, or
  configuration details absent from the report.
- Never claim to have inspected source code, run tests, reproduced a bug, opened
  a link, or verified behavior.
- When evidence is insufficient, reflect that in the classification, the
  components, the reasoning, and the confidence rather than filling gaps with
  assumptions.
"""


def triage_structured(title: str, body: str) -> Completion:
    """Phase 02: the same call, with a guaranteed shape on `.parsed`."""
    return complete_structured(
        STRUCTURED_SYSTEM,
        f"Title: {title}\n\n{body}",
        TriageResult,
        max_tokens=C.MAX_TOKENS,
    )


# Grounded triage. Phase 02's prompt, plus a section on the candidates. Kept as
# an addition rather than an edit so STRUCTURED_SYSTEM — and every Phase 02
# number — stays exactly what it was.
GROUNDED_SYSTEM = STRUCTURED_SYSTEM + """
**Candidate files**

After the report you are given candidate source files, each with the excerpt
that a similarity search matched. They are ranked by text similarity to the
report, not by relevance to the bug: the search cannot tell a file that
mentions the same words from the file that contains the fault.

In `suspected_files`, list up to five candidates the fix for this report would
most likely change, most likely first. Use paths exactly as given, and only
paths from the list. Judge each one by what its excerpt and path show it does,
against what the report says goes wrong; keep the search order only where the
evidence does not separate two files. An empty list is correct when no
candidate fits.

You have seen excerpts, not whole files. Say which excerpt supports a choice,
and do not claim to have read anything beyond them.
"""


def grounded_prompt(title: str, body: str, candidates: list) -> str:
    """The issue as filed, then each candidate in retrieval's rank order.

    `candidates` are retrieval.search.FileHit. The excerpt is the chunk that
    ranked the file, header included, so the model sees what the search saw.
    """
    parts = [f"Title: {title}\n\n{body}", "---\n\nCandidate files:"]
    for rank, f in enumerate(candidates, 1):
        parts.append(
            f"[{rank}] {f.path}:{f.best.start_line}-{f.best.end_line}\n"
            f"```\n{f.best.text}\n```"
        )
    return "\n\n".join(parts)


def check_candidates(result: GroundedTriageResult, candidates: list) -> None:
    """A path the model was not shown is a contract violation, not a guess.

    It is checked here rather than in the schema because the allowed set changes
    with every request. Raised as ValueError so the route and the eval treat it
    exactly like a maxLength breach — and count it.
    """
    allowed = {f.path for f in candidates}
    unknown = [p for p in result.suspected_files if p not in allowed]
    if unknown:
        raise ValueError(f"suspected_files not among the candidates: {unknown}")


def triage_grounded(title: str, body: str, candidates: list) -> Completion:
    """Structured triage with retrieval's candidates in the prompt.

    Retrieval is the caller's job, so the eval can score the same candidate
    list as the retrieval-only baseline without searching twice.
    """
    completion = complete_structured(
        GROUNDED_SYSTEM,
        grounded_prompt(title, body, candidates),
        GroundedTriageResult,
        max_tokens=C.MAX_TOKENS,
    )
    check_candidates(completion.parsed, candidates)
    return completion
