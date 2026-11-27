"""One on-demand inbox session: sync, choose, confirm the spend, triage, confirm each post.

    ENV_FILE=.env.<name> GITHUB_TOKEN=$(gh auth token) PROVIDER=anthropic python -m inbox
    python -m inbox score

Nothing here runs on a schedule, and there is no flag that skips a question:
every re-index, every dollar and every comment needs a typed "y". An empty
answer, "n" or end of input is always no. The session stops starting new
issues once its spend reaches --max-dollars; each run also keeps the agent's
own budgets.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

from api import config as C
from mining import config as M

from . import sync as sync_mod
from .app_auth import installation_token
from .github import GitHubError, Repo
from .triage import MARKER, RUNS, recommend, record, render, triage

TYPICAL = 0.002  # measured: the agent's $/triage on test with caching


def yes(answer: str) -> bool:
    return answer.strip().lower() in ("y", "yes")


def _recorded(path: Path) -> set[int]:
    """Issues with a recorded answer. A failed run doesn't count, so it's offered again."""
    if not path.exists():
        return set()
    recs = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    return {r["issue"] for r in recs if not r.get("error")}


def _choose(answer: str, numbers: list[int]) -> list[int]:
    a = answer.strip().lower()
    if a == "all":
        return list(numbers)
    picked = {int(t) for t in a.replace(",", " ").split() if t.isdigit()}
    return [n for n in numbers if n in picked]


def session(*, repo, repo_path, pin_path: Path = M.PINNED_SHA, ask=input, out=print,
            max_dollars: float = 0.10, again: frozenset[int] = frozenset(), triage_fn=triage,
            sync=sync_mod, runs_path: Path = RUNS,
            per_run_cap: float = C.AGENT_MAX_DOLLARS) -> dict:
    def q(prompt: str) -> str:
        try:
            return ask(prompt)
        except EOFError:
            return ""

    result = {"spent": 0.0, "triaged": [], "posted": [], "skipped": []}
    where = f"{repo.owner}/{repo.name}"
    pin = pin_path.read_text().strip()
    out(f"{where} at {pin[:7]}")
    head = sync.branch_head(repo_path, sync.default_branch(repo_path))
    sha = pin
    if head != pin:
        if yes(q(f"Branch moved {pin[:7]} -> {head[:7]}: re-pin and re-index? "
                 "(free, about a minute) [y/N] ")):
            try:
                sync.repin(head)
                sha = head
            except Exception as exc:  # the pin is put back; carry on at the old commit
                out(f"re-index failed ({exc}); keeping {pin[:7]}.")
        else:
            out(f"Keeping {pin[:7]}: answers describe the code as of that commit.")

    done = _recorded(runs_path)
    pending = [i for i in repo.open_issues()
               if i.number in again or (i.number not in done and not repo.has_marker(i.number, MARKER))]
    if not pending:
        out("Nothing new: every open issue has been triaged.")
        return result
    for i in pending:
        out(f"  #{i.number}  {i.title}")
    chosen = _choose(q(f"Triage which? [all / numbers / none] "), [i.number for i in pending])
    if not chosen:
        return result
    worst = min(len(chosen) * per_run_cap, max_dollars)
    if not yes(q(f"Spend up to ${worst:.2f} on {len(chosen)} issues (cap ${max_dollars:.2f}; "
                 f"typical about ${TYPICAL:.3f} each)? [y/N] ")):
        return result

    by_number = {i.number: i for i in pending}
    for n in chosen:
        left = max_dollars - result["spent"]
        if left <= 0:
            result["skipped"].append(n)
            continue
        iss = by_number[n]
        o = triage_fn(iss, sha, max_dollars=left)
        result["spent"] += o.cost_usd
        result["triaged"].append(n)
        if o.error:
            out(f"#{n}: no answer ({o.error}), ${o.cost_usd:.4f}")
            record(o, None, runs_path)
            continue
        out(f"#{n}: {', '.join(o.files)} · {o.issue_type} · confidence {o.confidence:.2f} · "
            f"${o.cost_usd:.4f}" + ("" if recommend(o) else
                                    " · probably not a code fix; not recommended to post"))
        url = None
        if yes(q(f"Post this comment to {where}#{n}? [y/N] ")):
            try:
                url = repo.post_comment(n, render(o, iss, repo.owner, repo.name))
                result["posted"].append(n)
                out(f"  posted {url}")
            except (GitHubError, httpx.HTTPError) as exc:  # the run is paid for: keep it
                out(f"  could not post to #{n}: {exc}")
        record(o, url, runs_path)
    if result["skipped"]:
        out(f"Stopped at the ${max_dollars:.2f} cap; not run: {result['skipped']}")
    out(f"Spent ${result['spent']:.4f} on {len(result['triaged'])} issues; "
        f"posted {len(result['posted'])}.")
    return result


def github_token(*, getenv=os.getenv, exchange=installation_token) -> tuple[str, str]:
    """The token to post with, and who it posts as: the GitHub App when GITHUB_APP_ID and
    GITHUB_APP_KEY_PATH are both set (in the corpus env file), else GITHUB_TOKEN. One
    without the other is an error, so a typo can't fall back to posting as you."""
    app_id, key_path = getenv("GITHUB_APP_ID") or "", getenv("GITHUB_APP_KEY_PATH") or ""
    if app_id and key_path:
        return (exchange(M.OWNER, M.NAME, app_id, Path(key_path).expanduser()),
                f"the GitHub App {app_id}")
    if app_id or key_path:
        missing = "GITHUB_APP_KEY_PATH" if app_id else "GITHUB_APP_ID"
        raise GitHubError(f"{missing} is not set: set both GITHUB_APP_ID and GITHUB_APP_KEY_PATH "
                          "to post as the app, or neither to use GITHUB_TOKEN")
    return getenv("GITHUB_TOKEN") or M.GITHUB_TOKEN, "GITHUB_TOKEN (as your account)"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m inbox")
    ap.add_argument("command", nargs="?", choices=["score"])
    ap.add_argument("--max-dollars", type=float, default=0.10)
    ap.add_argument("--again", type=int, nargs="*", default=[],
                    help="issue numbers to offer again even if already triaged")
    args = ap.parse_args(argv)
    if args.command == "score":
        from .score import main as score_main
        score_main()
        return
    if C.PROVIDER != "anthropic":
        raise SystemExit("the inbox needs PROVIDER=anthropic (tool use is Claude-only)")
    try:
        token, who = github_token()
        print(f"posting as {who}")
        session(repo=Repo(M.OWNER, M.NAME, token), repo_path=M.REPO_PATH,
                max_dollars=args.max_dollars, again=frozenset(args.again))
    except GitHubError as exc:
        sys.exit(str(exc))
