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


TOKEN_HELP = """
  Create one at https://github.com/settings/tokens
    "Generate new token (classic)" — no scopes needed for a public repo.
  Then put it in .env:
    GITHUB_TOKEN=ghp_your_real_token_here
"""


def _looks_like_placeholder(token: str) -> bool:
    """The .env.example value, left unedited. Catches the common first run."""
    return len(token) < 30 or set(token.split("_", 1)[-1]) <= set("x")


def _client() -> httpx.Client:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = C.GITHUB_TOKEN.strip()

    if not token:
        print("!  no GITHUB_TOKEN — 60 requests/hour, this will take hours")
        print(TOKEN_HELP)
    elif _looks_like_placeholder(token):
        raise SystemExit(
            "GITHUB_TOKEN in .env is still the placeholder from .env.example.\n"
            + TOKEN_HELP
        )
    else:
        headers["Authorization"] = f"Bearer {token}"

    return httpx.Client(headers=headers, timeout=30.0)


def _paginate(client: httpx.Client, path: str, params: dict[str, Any]) -> Iterator[dict]:
    """Walk a list endpoint by following GitHub's own `next` link.

    Counting pages by hand breaks past ~10,000 records: GitHub answers
    `page=100` with a 422 and tells you to use cursor pagination instead. The
    cursor is already in the Link header of every response, so following that
    link costs nothing extra and never hits the wall. Six years of Vite issues
    is well past it.
    """
    url: str | None = f"{API}{path}"
    query: dict[str, Any] | None = {**params, "per_page": 100}
    page = 1

    while url:
        response = client.get(url, params=query)

        if response.status_code == 403 and "rate limit" in response.text.lower():
            reset = int(response.headers.get("x-ratelimit-reset", "0"))
            wait = max(reset - time.time(), 5) + 2
            print(f"   rate limited — sleeping {wait:.0f}s")
            time.sleep(wait)
            continue
        if response.status_code == 401:
            raise SystemExit(
                "GitHub rejected the token (401). It is expired, revoked, or "
                "mistyped.\n" + TOKEN_HELP
            )
        response.raise_for_status()

        batch = response.json()
        if not batch:
            return
        yield from batch

        remaining = response.headers.get("x-ratelimit-remaining")
        print(f"   page {page:>3}  (+{len(batch)})  budget left: {remaining}", end="\r")

        # The next URL already carries every parameter, cursor included.
        url = response.links.get("next", {}).get("url")
        query = None
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
