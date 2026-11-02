import pytest

from eval import ablation_report as R


def rec(arm="grounded", issue=1, repeat=0, r1=0.0, r5=None, rr=None, gold=False,
        kind=None, cost=0.01, lat=1000, picked=(), calls=(), turns=1):
    return {"arm": arm, "repeat": repeat, "issue": issue, "mentions_gold_path": gold,
            "gold": [], "candidates": [], "picked": list(picked),
            "recall_at_1": r1, "recall_at_5": r1 if r5 is None else r5,
            "rr": r1 if rr is None else rr, "turns": turns, "tool_calls": list(calls),
            "input_tokens": 0, "output_tokens": 0, "cost_usd": cost, "latency_ms": lat,
            "error": None if kind is None else "x", "split": "val", "error_kind": kind}


def test_arm_scores_mean_and_spread():
    recs = [rec(repeat=i, issue=n, r1=1.0 if n < k else 0.0)
            for i, k in enumerate([3, 4, 5]) for n in range(10)]
    mean, lo, hi = R.arm_scores(recs, "grounded", "clean")["recall_at_1"]
    assert (round(mean, 9), round(lo, 9), round(hi, 9)) == (0.4, 0.3, 0.5)


def test_clean_subset_excludes_named_gold():
    recs = [rec(issue=1, r1=1.0), rec(issue=2, r1=0.0, gold=True)]
    assert R.arm_scores(recs, "grounded", "all")["recall_at_1"][0] == 0.5
    assert R.arm_scores(recs, "grounded", "clean")["recall_at_1"][0] == 1.0


def test_api_errors_are_excluded_from_scores():
    recs = [rec(issue=1, r1=1.0), rec(issue=2, r1=0.0, kind="api")]
    assert R.arm_scores(recs, "grounded", "all")["recall_at_1"][0] == 1.0
    assert R.cost_stats(recs, "grounded")["api_errors"] == 1


def test_run_errors_score_zero_and_count_cost():
    recs = [rec(issue=1, r1=1.0, cost=0.01), rec(issue=2, r1=0.0, kind="run", cost=0.03)]
    assert R.arm_scores(recs, "grounded", "all")["recall_at_1"][0] == 0.5
    cs = R.cost_stats(recs, "grounded")
    assert cs["run_errors"] == 1 and cs["per_triage"] == pytest.approx(0.02)
    assert cs["per_correct"] == pytest.approx(0.04)


def test_paired_counts_wins_losses_ties():
    a = [rec("grounded", i, r1=v) for i, v in enumerate([0, 1, 1, 0])]
    b = [rec("agent", i, r1=v) for i, v in enumerate([1, 0, 1, 0])]
    p = R.paired(a + b, "grounded", "agent", n_boot=200)
    assert (p["wins"], p["losses"], p["ties"]) == (1, 1, 2)
    assert p["diff"] == 0.0


def test_paired_averages_repeats_per_issue():
    a = [rec("grounded", 1, repeat=r, r1=0.0) for r in range(2)]
    b = [rec("agent", 1, repeat=0, r1=1.0), rec("agent", 1, repeat=1, r1=0.0)]
    p = R.paired(a + b, "grounded", "agent", n_boot=100)
    assert p["diff"] == 0.5 and p["wins"] == 1


def _gap(n=30, gap=1.0):
    return ([rec("grounded", i, r1=0.0) for i in range(n)]
            + [rec("agent", i, r1=gap) for i in range(n)])


def test_bootstrap_interval_excludes_zero_for_a_clear_gap():
    p = R.paired(_gap(), "grounded", "agent")
    assert p["ci_low"] > 0 and p["ci_high"] >= p["ci_low"]


def test_bootstrap_interval_includes_zero_for_no_gap():
    base = [rec("grounded", i, r1=float(i % 2)) for i in range(30)]
    twin = [rec("agent", i, r1=float(i % 2)) for i in range(30)]
    p = R.paired(base + twin, "grounded", "agent")
    assert p["diff"] == 0 and p["ci_low"] <= 0 <= p["ci_high"]


def test_bootstrap_interval_known_value():
    # diffs: 10 issues at +1, 10 at 0 -> mean .5, sd of resampled mean ~ .1118
    recs = ([rec("grounded", i, r1=0.0) for i in range(20)]
            + [rec("agent", i, r1=1.0 if i < 10 else 0.0) for i in range(20)])
    p = R.paired(recs, "grounded", "agent")
    assert p["diff"] == 0.5
    assert 0.27 < p["ci_low"] < 0.32 and 0.68 < p["ci_high"] < 0.73


def test_bootstrap_is_deterministic():
    recs = [rec("grounded", i, r1=float(i % 3 == 0)) for i in range(20)] + \
           [rec("agent", i, r1=float(i % 2 == 0)) for i in range(20)]
    assert R.paired(recs, "grounded", "agent") == R.paired(recs, "grounded", "agent")


def test_paired_returns_none_without_shared_issues():
    recs = [rec("grounded", 1), rec("agent", 2)]
    assert R.paired(recs, "grounded", "agent") is None
    assert R.paired(recs, "grounded", "agent-look") is None


def test_flip_rate():
    recs = []
    for issue, hits in {1: (1, 1), 2: (0, 0), 3: (1, 0), 4: (0, 1)}.items():
        recs += [rec("agent", issue, repeat=i, r1=h) for i, h in enumerate(hits)]
    assert R.flip_rate(recs, "agent", "all") == 0.5


def test_flip_rate_none_with_one_repeat():
    assert R.flip_rate([rec("agent", 1)], "agent", "all") is None


def test_cost_per_correct_is_none_without_hits():
    cs = R.cost_stats([rec(r1=0.0), rec(issue=2, r1=0.0)], "grounded")
    assert cs["per_correct"] is None and cs["per_triage"] == pytest.approx(0.01)


def test_picks_seen():
    calls = [{"name": "read_file", "input": {}, "is_error": False,
              "output_preview": "contents of src/a.py ..."}]
    recs = [rec("agent", 1, picked=["src/a.py", "src/b.py"], calls=calls, turns=2),
            rec("agent", 2, turns=1)]
    ts = R.tool_stats(recs, "agent")
    assert ts["picks_seen"] == 0.5 and ts["used_any"] == 0.5
    assert ts["calls_by_tool"] == {"read_file": 1} and ts["tool_error_rate"] == 0.0


def _arms(gaps):
    recs = [rec("grounded", i, r1=0.0) for i in range(30)]
    for arm, g in gaps.items():
        recs += [rec(arm, i, r1=g) for i in range(30)]
    return recs


def test_choose_arm_picks_best_qualifying_agent_arm():
    recs = _arms({"agent-no-tools": 0.0, "agent": 0.5, "agent-look": 1.0})
    assert R.choose_arm(recs) == "agent-look"


def test_choose_arm_falls_back_to_grounded():
    assert R.choose_arm(_arms({"agent-no-tools": 0.0, "agent": 0.0})) == "grounded"


def test_markdown_survives_a_missing_arm():
    recs = _arms({"agent": 1.0})
    md = R.markdown(recs)
    assert "no data" in md and "agent-look" in md


def test_markdown_prints_na_for_per_correct():
    assert "n/a" in R.markdown([rec("grounded", 1, r1=0.0)])


def test_report_flag_calls_report_and_builds_no_jobs(monkeypatch, tmp_path):
    from eval import ablation
    seen = []
    monkeypatch.setattr(R, "main", lambda p: seen.append(p))
    monkeypatch.setattr(ablation, "pending_jobs",
                        lambda *a, **k: pytest.fail("built jobs"))
    ablation.main(["--report", str(tmp_path / "x.jsonl")])
    assert seen == [str(tmp_path / "x.jsonl")]


def test_markdown_has_chain_rows_with_wins_and_losses():
    # issues 0-3. grounded: 0,0,0,1  no-tools: 1,0,0,1  agent: 1,1,0,0  look: 1,1,0,0
    vals = {"grounded": (0, 0, 0, 1), "agent-no-tools": (1, 0, 0, 1),
            "agent": (1, 1, 0, 0), "agent-look": (1, 1, 0, 0)}
    recs = [rec(a, i, r1=float(v)) for a, vs in vals.items() for i, v in enumerate(vs)]
    md = R.markdown(recs)
    chain = md.split("## Chain")[1].split("## ")[0]
    assert "| grounded -> agent-no-tools | 4 | 1 | 0 | 3 |" in chain
    assert "| agent-no-tools -> agent | 4 | 1 | 1 | 2 |" in chain
    assert "| agent -> agent-look | 4 | 0 | 0 | 4 |" in chain
    assert "no measurable difference" in chain


def test_markdown_chain_says_no_data_for_missing_arm():
    md = R.markdown(_arms({"agent": 1.0}))
    chain = md.split("## Chain")[1].split("## ")[0]
    assert "| grounded -> agent-no-tools | 0 | no data" in chain


def _with_retrieval():
    vals = {"retrieval": (0, 0, 0, 1), "grounded": (0, 1, 0, 1), "agent": (1, 1, 0, 0)}
    return [rec(a, i, r1=float(v)) for a, vs in vals.items() for i, v in enumerate(vs)]


def test_markdown_scores_include_retrieval():
    md = R.markdown(_with_retrieval())
    scores = md.split("## Scores")[1].split("## ")[0]
    assert "| retrieval | clean | 4 | 0.250 (0.250-0.250)" in scores


def test_markdown_vs_retrieval_wins_and_losses():
    md = R.markdown(_with_retrieval())
    sec = md.split("## Each model arm against retrieval (clean")[1].split("## ")[0]
    assert "| retrieval -> grounded | 4 | 1 | 0 | 3 |" in sec
    assert "| retrieval -> agent | 4 | 2 | 1 | 1 |" in sec


def test_markdown_vs_retrieval_says_no_data_without_retrieval_records():
    md = R.markdown(_arms({"agent": 1.0}))
    sec = md.split("## Each model arm against retrieval (clean")[1].split("## ")[0]
    assert "| retrieval -> agent | 0 | no data" in sec
