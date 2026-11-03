from eval.patch_report import summarize


def rec(issue, repeat=0, outcome="pass", src="hit", hit=True, cost=0.1, kind=None, **kw):
    r = {"issue": issue, "repeat": repeat, "source_outcome": src, "source_fault_hit": hit,
         "outcome": outcome, "cost_usd": cost, "error_kind": kind, "error": None}
    r.update(kw)
    return r


def test_shares_counts_and_min_max_across_repeats():
    recs = [rec(1, 0, "pass"), rec(2, 0, "test_fail"), rec(1, 1, "pass"), rec(2, 1, "pass")]
    s = summarize(recs, {})
    assert s["outcomes"]["pass"]["n"] == 3
    assert s["outcomes"]["pass"]["share"] == 0.75
    assert s["outcomes"]["pass"]["min"] == 0.5 and s["outcomes"]["pass"]["max"] == 1.0
    assert s["outcomes"]["test_fail"]["min"] == 0.0


def test_patchable_pass_rate_excludes_stale_path():
    recs = [rec(1, outcome="pass", cost=0.2), rec(2, outcome="stale_path", cost=0.0),
            rec(3, outcome="test_fail", cost=0.2)]
    s = summarize(recs, {})
    assert s["patchable_pass_rate"] == 0.5
    assert s["outcomes"]["stale_path"]["share"] == 1 / 3
    assert abs(s["cost_per_patch"] - 0.2) < 1e-9
    assert abs(s["cost_per_pass"] - 0.4) < 1e-9


def test_by_source_outcome_and_fault_hit():
    recs = [rec(1, src="hit", hit=True), rec(2, src="hit", hit=False, outcome="test_fail"),
            rec(3, src="hit", hit=None, outcome="test_fail"), rec(4, src="locate_miss", hit=None, outcome="test_fail")]
    s = summarize(recs, {})
    assert s["by_source_outcome"]["hit"] == {"n": 3, "pass": 1}
    assert s["by_source_outcome"]["locate_miss"] == {"n": 1, "pass": 0}
    assert s["by_fault_hit"] == {True: {"n": 1, "pass": 1}, False: {"n": 1, "pass": 0},
                                 None: {"n": 1, "pass": 0}}


def test_superseded_api_record_uses_retry_and_spend_counts_both():
    recs = [rec(1, outcome=None, kind="api", cost=0.3), rec(1, outcome="pass", cost=0.1)]
    s = summarize(recs, {})
    assert s["api_errors"] == 0 and s["errors"] == {}
    assert s["outcomes"]["pass"]["n"] == 1
    assert abs(s["cost_per_pass"] - 0.4) < 1e-9
    assert abs(s["cost_per_patch"] - 0.4) < 1e-9


def test_infra_superseded_by_reverify_counts_spend_once():
    recs = [rec(1, outcome=None, kind="infra", cost=0.25),
            rec(1, outcome="pass", cost=0.0, reverified_from_infra=True)]
    s = summarize(recs, {})
    assert s["errors"] == {}
    assert s["outcomes"]["pass"]["n"] == 1
    assert abs(s["cost_per_pass"] - 0.25) < 1e-9


def test_final_api_and_unexpected_errors_excluded_and_counted():
    recs = [rec(1, outcome="pass", cost=0.1), rec(2, outcome=None, kind="api", cost=0.2),
            rec(3, outcome="patch_error", kind="unexpected", cost=0.3)]
    s = summarize(recs, {})
    assert s["errors"] == {"api": 1, "unexpected": 1}
    assert s["api_errors"] == 1
    assert s["outcomes"]["pass"]["share"] == 1.0
    assert s["patchable_pass_rate"] == 1.0
    assert abs(s["cost_per_patch"] - 0.6) < 1e-9
    assert abs(s["cost_per_pass"] - 0.6) < 1e-9
    assert s["by_source_outcome"] == {"hit": {"n": 1, "pass": 1}}


def test_cost_per_pass_none_without_passes():
    s = summarize([rec(1, outcome="test_fail")], {})
    assert s["cost_per_pass"] is None
    assert s["patchable_pass_rate"] == 0.0


def test_harness_invalid_sorted_with_reasons():
    h = {5: {"issue": 5, "valid": False, "reason": "no fail"},
         2: {"issue": 2, "valid": False, "reason": "timeout"},
         3: {"issue": 3, "valid": True, "reason": None}}
    assert summarize([], h)["harness_invalid"] == [(2, "timeout"), (5, "no fail")]


def test_empty_records():
    s = summarize([], {})
    assert s["outcomes"] == {} and s["patchable_pass_rate"] is None
    assert s["cost_per_patch"] is None and s["cost_per_pass"] is None
