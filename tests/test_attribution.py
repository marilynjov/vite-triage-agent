from eval.attribution import outcome, overlaps

G = ["a.py"]


def stages(locate=("a.py",), regions=((("a.py", 10, 20),)), hyp=("a.py",), fault=("a.py", 12, 14)):
    return {
        "locate": {"reasoning": "", "files": [{"path": p, "why": "", "evidence": ""} for p in locate]},
        "understand": {
            "reasoning": "",
            "regions": [
                {"path": p, "symbol": "", "start_line": s, "end_line": e, "what_it_does": "", "relevance": ""}
                for p, s, e in regions
            ],
        },
        "hypothesis": {
            "suspected_files": list(hyp),
            "fault": None if fault is None else {"path": fault[0], "start_line": fault[1], "end_line": fault[2], "mechanism": ""},
        },
    }


def test_error_outcome_names_the_stage():
    assert outcome({"locate": None, "understand": None, "hypothesis": None}, "locate", G, {"a.py": [[1, 2]]}) == ("error:locate", None)
    assert outcome(stages(), "hypothesize", G, {"a.py": [[10, 20]]})[0] == "error:hypothesize"


def test_locate_miss():
    assert outcome(stages(locate=("b.py",)), None, G, {"a.py": [[10, 20]]}) == ("locate_miss", None)


def test_understand_miss_with_region_gold():
    s = stages(regions=(("a.py", 50, 60),))
    assert outcome(s, None, G, {"a.py": [[10, 20]]}) == ("understand_miss", None)


def test_understand_miss_file_level_when_no_region_gold():
    assert outcome(stages(regions=(("b.py", 1, 5),)), None, G, {"a.py": []})[0] == "understand_miss"
    assert outcome(stages(regions=(("a.py", 1, 5),)), None, G, {"a.py": []})[0] == "hit"


def test_hypothesize_miss():
    assert outcome(stages(hyp=("b.py", "a.py")), None, G, {"a.py": [[10, 20]]}) == ("hypothesize_miss", None)


def test_empty_suspected_files_is_not_a_hit():
    assert outcome(stages(hyp=()), None, G, {"a.py": [[10, 20]]})[0] == "hypothesize_miss"


def test_hit_with_fault_hit_true_and_false():
    assert outcome(stages(), None, G, {"a.py": [[10, 20]]}) == ("hit", True)
    assert outcome(stages(fault=("a.py", 30, 40)), None, G, {"a.py": [[10, 20]]}) == ("hit", False)
    assert outcome(stages(fault=("b.py", 10, 20)), None, G, {"a.py": [[10, 20]]}) == ("hit", False)


def test_fault_hit_is_none_without_region_gold():
    assert outcome(stages(), None, G, {"a.py": []}) == ("hit", None)


def test_touching_ranges_overlap():
    assert overlaps([10, 20], [20, 30])
    assert overlaps([5, 5], [5, 5])
    assert not overlaps([10, 19], [20, 30])


def test_mixed_row_applies_gold_per_file():
    gold = ["a.py", "b.py"]
    regions = {"a.py": [[10, 20]], "b.py": []}
    # region in b.py (file-level gold) reaches gold even though a.py has ranges
    s = stages(locate=("b.py",), regions=(("b.py", 1, 2),), hyp=("b.py",), fault=("a.py", 15, 16))
    assert outcome(s, None, gold, regions) == ("hit", True)
    # region in a.py outside its ranges does not reach gold
    s = stages(locate=("a.py",), regions=(("a.py", 50, 60),))
    assert outcome(s, None, gold, regions) == ("understand_miss", None)
    # fault outside every range: row has region gold, so False not None
    s = stages(locate=("b.py",), regions=(("b.py", 1, 2),), hyp=("b.py",), fault=("a.py", 50, 60))
    assert outcome(s, None, gold, regions) == ("hit", False)


def test_fault_in_file_level_only_gold_file_is_unjudged():
    gold = ["a.py", "b.py"]
    regions = {"a.py": [[10, 20]], "b.py": []}
    s = stages(locate=("a.py",), regions=(("a.py", 10, 20),), hyp=("a.py",), fault=("b.py", 1, 2))
    assert outcome(s, None, gold, regions) == ("hit", None)
    s = stages(locate=("a.py",), regions=(("a.py", 10, 20),), hyp=("a.py",), fault=("c.py", 1, 2))
    assert outcome(s, None, gold, regions) == ("hit", False)
