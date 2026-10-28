from eval.agent import found_outside


def test_found_outside_counts_only_gold_missing_from_candidates():
    assert found_outside(["a", "b", "c"], ["a"], {"a", "b", "z"}) == {"b"}
