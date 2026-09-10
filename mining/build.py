"""Stage 3 — join everything into labeled examples, and report the yield.

The chain we are reconstructing:

    issue  --(closing keyword in a PR body)-->  PR  --(#ref in a commit)-->  commit  -->  files

Plus a shortcut for commits that close an issue directly in their own message.
Every issue that survives the filters becomes one training example.
"""
from __future__ import annotations

import re
from collections import defaultdict

from . import config as C
from . import gitlog, jsonl

CLOSES = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+"
    r"(?:#|https?://github\.com/[\w.-]+/[\w.-]+/issues/)(\d+)",
    re.I,
)


def build() -> None:
    if not C.PINNED_SHA.exists():
        raise SystemExit("no pinned sha — run `python -m mining commits` first")
    sha = C.PINNED_SHA.read_text().strip()
    existing = gitlog.tracked_files(sha)

    commits = list(jsonl.read(C.COMMITS_JSONL))
    issues = {row["number"]: row for row in jsonl.read(C.ISSUES_JSONL)}
    pulls = list(jsonl.read(C.PULLS_JSONL))

    # PR number -> the files its commit(s) touched
    pr_files: dict[int, set[str]] = defaultdict(set)
    # issue number -> files, for commits that close an issue directly
    direct_files: dict[int, set[str]] = defaultdict(set)
    for commit in commits:
        for pr in commit["pr_refs"]:
            pr_files[pr].update(commit["files"])
        for issue_no in commit["closes_refs"]:
            direct_files[issue_no].update(commit["files"])

    # issue number -> the PRs whose body says they close it
    issue_prs: dict[int, set[int]] = defaultdict(set)
    for pull in pulls:
        text = f"{pull['title']}\n{pull['body']}"
        for issue_no in {int(n) for n in CLOSES.findall(text)}:
            issue_prs[issue_no].add(pull["number"])

    counters = defaultdict(int)
    rows = []

    for number, issue in issues.items():
        counters["closed_issues"] += 1

        touched: set[str] = set(direct_files.get(number, set()))
        linked_prs = sorted(issue_prs.get(number, set()))
        for pr in linked_prs:
            touched |= pr_files.get(pr, set())

        if not touched:
            counters["no_linked_fix"] += 1
            continue
        counters["linked_to_a_fix"] += 1

        gold = sorted(f for f in touched if C.is_gold_candidate(f))
        if not gold:
            counters["fix_outside_scope"] += 1
            continue
        counters["in_scope"] += 1

        missing = [f for f in gold if f not in existing]
        if missing:
            # The file was renamed or deleted between the fix and our pinned
            # index. We could never retrieve it, so the label is unusable.
            gold = [f for f in gold if f in existing]
            if not gold:
                counters["gold_gone_at_pin"] += 1
                continue

        if len(gold) > C.MAX_GOLD_FILES:
            counters["too_many_files"] += 1
            continue

        body = (issue["body"] or "").strip()
        if len(body) < C.MIN_BODY_CHARS:
            counters["body_too_short"] += 1
            continue

        # Leakage check: if the issue text literally names a gold file, finding
        # it is string matching, not retrieval. Keep the row, flag it, and
        # report the rate — you will want to score with and without these.
        haystack = f"{issue['title']}\n{body}"
        leaks = any(
            g in haystack or g.rsplit("/", 1)[-1] in haystack for g in gold
        )
        if leaks:
            counters["names_a_gold_file"] += 1

        rows.append({
            "issue": number,
            "title": issue["title"],
            "body": body,
            "created_at": issue["created_at"],
            "labels": issue["labels"],
            "linked_prs": linked_prs,
            "gold_files": gold,
            "n_gold": len(gold),
            "mentions_gold_path": leaks,
        })
        counters["usable"] += 1

    jsonl.write(C.DATASET_JSONL, rows)
    _report(counters, rows, sha)


def _report(counters, rows, sha) -> None:
    total = counters["closed_issues"] or 1
    print()
    print(f"  pinned at {sha[:10]}   scope {C.SCOPE_PREFIX}")
    print("  " + "-" * 56)

    funnel = [
        ("closed issues fetched", "closed_issues"),
        ("dropped: no linked fix", "no_linked_fix"),
        ("dropped: fix outside scope", "fix_outside_scope"),
        ("dropped: gold file gone at pin", "gold_gone_at_pin"),
        (f"dropped: >{C.MAX_GOLD_FILES} files changed", "too_many_files"),
        (f"dropped: body <{C.MIN_BODY_CHARS} chars", "body_too_short"),
    ]
    for label, key in funnel:
        value = counters[key]
        print(f"  {label:<32} {value:>7,}  {value / total:>5.1%}")

    usable = counters["usable"]
    leaky = counters["names_a_gold_file"]
    print("  " + "-" * 56)
    print(f"  {'USABLE EXAMPLES':<32} {usable:>7,}  {usable / total:>5.1%}")
    if usable:
        avg = sum(r["n_gold"] for r in rows) / usable
        single = sum(1 for r in rows if r["n_gold"] == 1)
        print(f"  {'  mean gold files':<32} {avg:>7.2f}")
        print(f"  {'  single-file fixes':<32} {single:>7,}  {single / usable:>5.1%}")
        print(f"  {'  issue text names a gold file':<32} {leaky:>7,}  {leaky / usable:>5.1%}")
    print("  " + "-" * 56)

    if usable >= 300:
        print(f"\n  GATE PASSED — {usable:,} examples. Vite is your corpus.")
    elif usable >= 150:
        print(f"\n  MARGINAL — {usable:,} examples. Workable, but try widening")
        print("  SINCE_YEARS or raising MAX_GOLD_FILES before committing.")
    else:
        print(f"\n  GATE FAILED — {usable:,} examples is too few to measure with.")
        print("  Re-point at TanStack Query, then date-fns, before going further.")
    print(f"\n  → {C.DATASET_JSONL}")
