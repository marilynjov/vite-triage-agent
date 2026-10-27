"""score_ranking: one definition of recall@k and MRR for every ranker."""
from __future__ import annotations

from eval.retrieve import score_ranking


def test_score_ranking_is_fraction_of_gold_and_first_hit_reciprocal():
    s = score_ranking(["c.ts", "a.ts"], {"a.ts", "b.ts"}, [1, 5])
    assert s == {"recall@1": 0.0, "recall@5": 0.5, "mrr": 0.5}


def test_score_ranking_counts_a_repeated_path_once():
    s = score_ranking(["a.ts", "a.ts"], {"a.ts", "b.ts"}, [5])
    assert s["recall@5"] == 0.5
