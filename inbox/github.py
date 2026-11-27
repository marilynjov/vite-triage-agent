"""One repository's issues and comments over GitHub's REST API.

Reads open issues (pull requests, which /issues also returns, are dropped),
looks for the inbox's marker in an issue's comments, and posts a comment.
Pages are walked by following the Link header's `next`, as mining/gh.py does.
A refusal (401/403/404) is usually a token that cannot see a private repo, so
the error says how to use the gh CLI's token, which has `repo` scope.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

API = "https://api.github.com"


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    created_at: str
    url: str


class GitHubError(Exception):
    pass


class Repo:
    def __init__(self, owner: str, name: str, token: str, *,
                 transport: httpx.BaseTransport | None = None):
        self.owner, self.name = owner, name
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(base_url=API, headers=headers, timeout=30.0, transport=transport)

    def _check(self, r: httpx.Response, path: str) -> httpx.Response:
        if r.status_code in (401, 403, 404):
            raise GitHubError(f"GitHub refused {path} ({r.status_code}). For a private repo use "
                              "GITHUB_TOKEN=$(gh auth token), which has repo scope.")
        r.raise_for_status()
        return r

    def _pages(self, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        url: str | None = path
        query: dict | None = {**params, "per_page": 100}
        while url:
            r = self._check(self._http.get(url, params=query), path)
            out.extend(r.json())
            url = r.links.get("next", {}).get("url")
            query = None  # the next link carries its own query
        return out

    def open_issues(self) -> list[Issue]:
        items = self._pages(f"/repos/{self.owner}/{self.name}/issues", {"state": "open"})
        return sorted((Issue(i["number"], i["title"], i.get("body") or "", i["created_at"],
                             i["html_url"])
                       for i in items if "pull_request" not in i), key=lambda i: i.number)

    def has_marker(self, number: int, marker: str) -> bool:
        comments = self._pages(f"/repos/{self.owner}/{self.name}/issues/{number}/comments", {})
        return any(marker in (c.get("body") or "") for c in comments)

    def post_comment(self, number: int, body: str) -> str:
        path = f"/repos/{self.owner}/{self.name}/issues/{number}/comments"
        return self._check(self._http.post(path, json={"body": body}), path).json()["html_url"]
