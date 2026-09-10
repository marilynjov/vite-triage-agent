"""Stage 2 — pull issue and PR text from the GitHub API.

Bulk list endpoints only: 100 records per request instead of one request per
issue. Vite's whole recent history costs a few hundred requests, well inside
the authenticated budget of 5000/hour.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import httpx

from . import config as C
from . import jsonl

API = "https://api.github.com"


def _client() -> httpx.Client:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if C.GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {C.GITHUB_TOKEN}"
    else:
        print("!  no GITHUB_TOKEN — you get 60 requests/hour and this will crawl")
    return httpx.Client(headers=headers, timeout=30.0)


def _paginate(client: httpx.Client, path: str, params: dict[str, Any]) -> Iterator[dict]:
    page = 1
    while True:
        params = {**params, "per_page": 100, "page": page}
        response = client.get(f"{API}{path}", params=params)

        if response.status_code == 403 and "rate limit" in response.text.lower():
            reset = int(response.headers.get("x-ratelimit-reset", "0"))
            wait = max(reset - time.time(), 5) + 2
            print(f"   rate limited — sleeping {wait:.0f}s")
            time.sleep(wait)
            continue
        response.raise_for_status()

        batch = response.json()
        if not batch:
            return
        yield from batch

        remaining = response.headers.get("x-ratelimit-remaining")
        print(f"   page {page:>3}  (+{len(batch)})  budget left: {remaining}", end="\r")
        if len(batch) < 100:
            return
        page += 1


def fetch() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=365 * C.SINCE_YEARS)
    since = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    base = f"/repos/{C.OWNER}/{C.NAME}"

    with _client() as client:
        print(f"issues since {since[:10]}")
        issues = []
        for item in _paginate(client, f"{base}/issues",
                              {"state": "closed", "since": since, "sort": "updated"}):
            # This endpoint returns pull requests too. They are not issues.
            if "pull_request" in item:
                continue
            issues.append({
                "number": item["number"],
                "title": item.get("title") or "",
                "body": item.get("body") or "",
                "created_at": item.get("created_at"),
                "closed_at": item.get("closed_at"),
                "labels": [l["name"] for l in item.get("labels", [])],
            })
        print(f"\nissues kept      {len(issues):,}")

        print("pull requests")
        pulls = []
        for item in _paginate(client, f"{base}/pulls",
                              {"state": "closed", "sort": "created", "direction": "desc"}):
            created = item.get("created_at") or ""
            if created and created < since:
                break  # sorted newest-first, so everything after this is older
            pulls.append({
                "number": item["number"],
                "title": item.get("title") or "",
                "body": item.get("body") or "",
                "merged_at": item.get("merged_at"),
                "created_at": created,
            })
        print(f"\npulls kept       {len(pulls):,}")

    jsonl.write(C.ISSUES_JSONL, issues)
    jsonl.write(C.PULLS_JSONL, pulls)
    print(f"→ {C.ISSUES_JSONL}")
    print(f"→ {C.PULLS_JSONL}")
