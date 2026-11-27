"""Triage one issue at a chosen commit, render the suggestion as a comment, record the run.

The same pieces the Agent button uses: retrieval's top candidates, then the
tool-using agent, with the tools reading `sha` (the branch head the session
synced to) and git_log cut off at the issue's creation. A failed run is an
outcome with its error and cost, never an exception, so one bad issue can't end
a session.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from agent import loop, tools
from api import config as C
from mining import config as M
from retrieval import config as R, search

from .github import Issue

MARKER = "<!-- vite-triage-agent -->"
RUNS = M.DATA / "live" / "runs.jsonl"
NOT_CODE = frozenset({"enhancement", "documentation", "question"})
MIN_CONFIDENCE = 0.5
REASONING_CHARS = 1500


@dataclass
class Outcome:
    issue: int
    sha: str
    files: list[str]
    issue_type: str | None
    confidence: float | None
    reasoning: str
    turns: int
    tools: dict[str, int]
    cost_usd: float
    error: str | None


def _from_run(issue: Issue, sha: str, run: loop.Run, error: str | None) -> Outcome:
    r = run.result if error is None else None
    return Outcome(issue=issue.number, sha=sha, files=list(r.suspected_files) if r else [],
                   issue_type=r.issue_type if r else None, confidence=r.confidence if r else None,
                   reasoning=r.reasoning if r else "", turns=len(run.turns),
                   tools=dict(Counter(c.name for c in run.trace)),
                   cost_usd=float(run.cost_usd), error=error)


def triage(issue: Issue, sha: str, *, search_fn=search.search_ranked, run_fn=loop.run) -> Outcome:
    candidates = search_fn(f"{issue.title}\n\n{issue.body}", strategy=R.DEFAULT_STRATEGY,
                           k=C.GROUNDED_K, corpus=R.CORPUS)
    with tools.at_rev(sha):
        try:
            run = run_fn(issue.title, issue.body, candidates, issue.created_at)
        except loop.RunError as exc:
            return _from_run(issue, sha, exc.run, str(exc))
    return _from_run(issue, sha, run, None)


def recommend(o: Outcome) -> bool:
    """Worth posting: a valid answer, about a code fix, with some confidence."""
    return (o.error is None and o.issue_type not in NOT_CODE
            and (o.confidence or 0.0) >= MIN_CONFIDENCE)


def render(o: Outcome, issue: Issue, owner: str, name: str) -> str:
    files = "\n".join(f"{i}. [`{f}`](https://github.com/{owner}/{name}/blob/{o.sha}/{f})"
                      for i, f in enumerate(o.files, 1))
    used = ", ".join(f"{n}×{t}" for t, n in sorted(o.tools.items())) or "no tools"
    return (f"**Where the fix probably goes** (at `{o.sha[:7]}`)\n\n{files}\n\n"
            f"{o.reasoning[:REASONING_CHARS]}\n\n"
            f"Type: {o.issue_type} · confidence {o.confidence:.2f} · {o.turns} turns · {used} · "
            f"${o.cost_usd:.4f}\n\n"
            f"_Automated suggestion; may be wrong._\n\n{MARKER}\n")


def record(o: Outcome, posted_url: str | None, path: Path = RUNS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {**asdict(o), "posted_url": posted_url,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")
