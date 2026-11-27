"""inbox.session: every spend and post needs a typed yes. GitHub, git and the agent are faked."""
from types import SimpleNamespace as NS

import pytest

from inbox import session as S
from inbox.github import Issue
from inbox.triage import MARKER, Outcome, record


def issue(n):
    return Issue(n, f"title {n}", "body", "2026-01-01T00:00:00Z", f"https://x/{n}")


class FakeRepo:
    owner, name = "acme", "widgets"

    def __init__(self, numbers, marked=()):
        self.issues = [issue(n) for n in numbers]
        self.marked = set(marked)
        self.posted = []

    def open_issues(self):
        return self.issues

    def has_marker(self, number, marker):
        assert marker == MARKER
        return number in self.marked

    def post_comment(self, number, body):
        self.posted.append(number)
        return f"https://x/{number}#c"


def outcome(n, *, cost=0.002, **kw):
    base = dict(issue=n, sha="head", files=["src/a.py"], issue_type="bug", confidence=0.9,
                reasoning="r", turns=1, tools={}, cost_usd=cost, error=None)
    return Outcome(**{**base, **kw})


class Harness:
    def __init__(self, tmp_path, numbers, answers, *, head="pin", marked=(), cost=0.002, **kw):
        self.pin = tmp_path / "pinned_sha.txt"
        self.pin.write_text("pin\n")
        self.runs = tmp_path / "live" / "runs.jsonl"
        self.repo = FakeRepo(numbers, marked)
        self.answers = list(answers)
        self.prompts, self.ran, self.repinned, self.budgets, self.said = [], [], [], [], []
        self.sync = NS(default_branch=lambda p: "main", branch_head=lambda p, b: head,
                       repin=lambda sha: self.repinned.append(sha))
        self.cost, self.kw = cost, kw
        self.outcomes = {}

    def ask(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError(f"unexpected question: {prompt}")
        a = self.answers.pop(0)
        if a is EOFError:
            raise EOFError
        return a

    def triage(self, iss, sha, max_dollars=None):
        self.ran.append((iss.number, sha))
        self.budgets.append(max_dollars)
        return self.outcomes.get(iss.number) or outcome(iss.number, sha=sha, cost=self.cost)

    def go(self, **kw):
        return S.session(repo=self.repo, repo_path="clone", pin_path=self.pin, ask=self.ask,
                         out=lambda *a: self.said.append(" ".join(map(str, a))),
                         triage_fn=self.triage, sync=self.sync,
                         runs_path=self.runs, **{**self.kw, **kw})


NO = ["", "n", "N", "no", EOFError]


@pytest.mark.parametrize("no", NO)
def test_declining_the_reindex_keeps_the_pin(tmp_path, no):
    h = Harness(tmp_path, [1], [no, "none"], head="newhead")
    h.go()
    assert h.repinned == [] and h.ran == []


def test_yes_to_reindex_repins_and_reads_the_new_head(tmp_path):
    h = Harness(tmp_path, [1], ["y", "all", "y", "n"], head="newhead")
    h.go()
    assert h.repinned == ["newhead"] and h.ran == [(1, "newhead")]


@pytest.mark.parametrize("no", NO)
def test_declining_which_or_spend_runs_nothing(tmp_path, no):
    h = Harness(tmp_path, [1, 2], [no])
    assert h.go()["triaged"] == [] and h.ran == []
    h2 = Harness(tmp_path, [1, 2], ["all", no])
    assert h2.go()["triaged"] == [] and h2.ran == []


@pytest.mark.parametrize("no", NO)
def test_declining_the_post_posts_nothing_but_records_the_run(tmp_path, no):
    h = Harness(tmp_path, [1], ["all", "yes", no])
    out = h.go()
    assert out["triaged"] == [1] and out["posted"] == [] and h.repo.posted == []
    assert h.runs.exists() and '"posted_url": null' in h.runs.read_text()


def test_an_unmoved_branch_asks_nothing_about_reindexing(tmp_path):
    h = Harness(tmp_path, [1], ["none"])
    h.go()
    assert len(h.prompts) == 1 and "Triage which" in h.prompts[0]


def test_issues_with_the_marker_or_a_recorded_run_are_not_offered(tmp_path):
    h = Harness(tmp_path, [1, 2, 3], ["all", "y", "n"], marked=[1])
    record(outcome(2), None, h.runs)
    h.go()
    assert [n for n, _ in h.ran] == [3]
    again = Harness(tmp_path, [1, 2, 3], ["2", "y", "n"], marked=[1])
    again.go(again={2})
    assert [n for n, _ in again.ran] == [2]


def test_nothing_new_asks_nothing(tmp_path):
    h = Harness(tmp_path, [1], [], marked=[1])
    assert h.go()["triaged"] == [] and h.prompts == []


def test_choosing_numbers_runs_only_those(tmp_path):
    h = Harness(tmp_path, [1, 2, 3], ["3, 1, 99", "y", "n", "n"])
    h.go()
    assert [n for n, _ in h.ran] == [1, 3]


def test_the_cap_stops_new_runs_and_lists_the_rest_as_skipped(tmp_path):
    h = Harness(tmp_path, [1, 2, 3, 4], ["all", "y", "n", "n", "n"], cost=0.04)
    out = h.go(max_dollars=0.10)
    assert out["triaged"] == [1, 2, 3] and out["skipped"] == [4]
    assert out["spent"] == pytest.approx(0.12)


def test_the_spend_question_shows_count_cap_and_worst_case(tmp_path):
    h = Harness(tmp_path, [1, 2], ["all", "n"])
    h.go(max_dollars=0.10, per_run_cap=0.05)
    spend = h.prompts[-1]
    assert "2 issues" in spend and "$0.10" in spend and "0.002" in spend


def test_a_post_needs_its_own_yes(tmp_path):
    h = Harness(tmp_path, [1, 2], ["all", "y", "y", "n"])
    out = h.go()
    assert out["posted"] == [1] and h.repo.posted == [1]
    assert sum("Post this comment" in p for p in h.prompts) == 2


def test_a_failed_run_offers_no_post(tmp_path):
    h = Harness(tmp_path, [1], ["all", "y"])
    h.outcomes[1] = outcome(1, files=[], error="tokens budget reached", issue_type=None,
                            confidence=None)
    out = h.go()
    assert out["triaged"] == [1] and not any("Post" in p for p in h.prompts)


def test_yes_accepts_only_y_or_yes():
    assert all(S.yes(a) for a in ["y", "Y", " yes ", "YES"])
    assert not any(S.yes(a) for a in ["", "n", "no", "yeah", "ok"])


def test_the_cli_refuses_without_the_anthropic_provider(monkeypatch):
    monkeypatch.setattr(S.C, "PROVIDER", "ollama")
    with pytest.raises(SystemExit, match="PROVIDER=anthropic"):
        S.main([])


# --- review fixes ---

def test_each_run_gets_only_what_is_left_of_the_cap(tmp_path):
    h = Harness(tmp_path, [1, 2, 3, 4], ["all", "y", "n", "n", "n"], cost=0.04)
    out = h.go(max_dollars=0.10)
    assert h.budgets == pytest.approx([0.10, 0.06, 0.02]) and out["skipped"] == [4]


def test_a_failed_post_still_records_the_run_and_the_session_goes_on(tmp_path):
    from inbox.github import GitHubError
    h = Harness(tmp_path, [1, 2], ["all", "y", "y", "n"])

    def refuse(number, body):
        raise GitHubError("GitHub refused /x (403)")

    h.repo.post_comment = refuse
    out = h.go()
    assert out["triaged"] == [1, 2] and out["posted"] == []
    assert len(h.runs.read_text().splitlines()) == 2
    assert any("could not post" in s for s in h.said)


def test_a_failed_reindex_keeps_the_old_pin_and_goes_on(tmp_path):
    h = Harness(tmp_path, [1], ["y", "all", "y", "n"], head="newhead")

    def boom(sha):
        raise RuntimeError("ollama down")

    h.sync.repin = boom
    h.go()
    assert h.ran == [(1, "pin")] and any("re-index failed" in s for s in h.said)


def test_the_repo_is_named_first_and_in_the_post_question(tmp_path):
    h = Harness(tmp_path, [1], ["all", "y", "n"])
    h.go()
    assert h.said[0].startswith("acme/widgets")
    assert any("acme/widgets#1" in p for p in h.prompts if "Post" in p)


def test_a_failed_run_is_offered_again_next_time(tmp_path):
    h = Harness(tmp_path, [1, 2], ["all", "y"])
    record(outcome(1, files=[], error="API error", issue_type=None, confidence=None), None, h.runs)
    record(outcome(2), None, h.runs)
    h.answers = ["all", "n"]
    h.go()
    assert "#1" in " ".join(h.said) and "#2" not in " ".join(s for s in h.said if s.strip().startswith("#"))
