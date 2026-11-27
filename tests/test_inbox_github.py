"""inbox.github: one repo's issues and comments, over a mocked transport. No network."""
import json

import httpx
import pytest

from inbox.github import GitHubError, Issue, Repo

API = "https://api.github.com"


def _issue(n, *, pr=False, body="b"):
    d = {"number": n, "title": f"t{n}", "body": body, "created_at": "2026-01-01T00:00:00Z",
         "html_url": f"https://github.com/acme/widgets/issues/{n}"}
    if pr:
        d["pull_request"] = {"url": "x"}
    return d


def _repo(handler):
    return Repo("acme", "widgets", "tok", transport=httpx.MockTransport(handler))


def test_open_issues_drops_prs_and_pages():
    def handler(req):
        assert req.headers["Authorization"] == "Bearer tok"
        if req.url.params.get("page") == "2":
            return httpx.Response(200, json=[_issue(1, body=None)])
        assert req.url.path == "/repos/acme/widgets/issues" and req.url.params["state"] == "open"
        nxt = f'<{API}/repos/acme/widgets/issues?state=open&page=2>; rel="next"'
        return httpx.Response(200, json=[_issue(3), _issue(2, pr=True)], headers={"Link": nxt})

    issues = _repo(handler).open_issues()
    assert [i.number for i in issues] == [1, 3]
    assert issues[0] == Issue(1, "t1", "", "2026-01-01T00:00:00Z",
                              "https://github.com/acme/widgets/issues/1")


def test_has_marker():
    def handler(req):
        assert req.url.path == "/repos/acme/widgets/issues/7/comments"
        return httpx.Response(200, json=[{"body": "hi"}, {"body": "x <!-- m --> y"}])

    repo = _repo(handler)
    assert repo.has_marker(7, "<!-- m -->") and not repo.has_marker(7, "<!-- other -->")


def test_post_comment_sends_the_body_and_returns_the_url():
    seen = {}

    def handler(req):
        seen.update(method=req.method, path=req.url.path, body=json.loads(req.content))
        return httpx.Response(201, json={"html_url": "https://github.com/acme/widgets/issues/7#c1"})

    assert _repo(handler).post_comment(7, "hello") == "https://github.com/acme/widgets/issues/7#c1"
    assert seen == {"method": "POST", "path": "/repos/acme/widgets/issues/7/comments",
                    "body": {"body": "hello"}}


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_refusal_names_the_gh_token_fix(status):
    with pytest.raises(GitHubError, match=r"GITHUB_TOKEN=\$\(gh auth token\)"):
        _repo(lambda req: httpx.Response(status, json={"message": "no"})).open_issues()
