"""Assign each staged run exactly one outcome (spec §2, "Outcome per run")."""


def overlaps(a: list[int], b: list[int]) -> bool:
    """Inclusive line-range overlap."""
    return a[0] <= b[1] and b[0] <= a[1]


def outcome(
    stages: dict,
    failed_stage: str | None,
    gold_files: list[str],
    regions: dict[str, list[list[int]]],
) -> tuple[str, bool | None]:
    """Return (outcome, fault_hit). Gold is applied per gold file."""
    if failed_stage:
        return f"error:{failed_stage}", None
    gold = set(gold_files)

    located = {f["path"] for f in ((stages.get("locate") or {}).get("files") or [])}
    if not (located & gold):
        return "locate_miss", None

    def reaches_gold(r: dict) -> bool:
        path = r["path"]
        if path not in gold:
            return False
        ranges = regions.get(path) or []
        if not ranges:
            return True  # file-level fallback for this gold file
        return any(overlaps([r["start_line"], r["end_line"]], g) for g in ranges)

    understood = (stages.get("understand") or {}).get("regions") or []
    if not any(reaches_gold(r) for r in understood):
        return "understand_miss", None

    hyp = stages.get("hypothesis") or {}
    suspected = hyp.get("suspected_files") or []
    if not suspected or suspected[0] not in gold:
        return "hypothesize_miss", None

    gold_ranges = {p: rs for p, rs in regions.items() if p in gold and rs}
    if not gold_ranges:
        return "hit", None
    fault = hyp.get("fault")
    if not fault:
        return "hit", False
    span = [fault["start_line"], fault["end_line"]]
    return "hit", any(overlaps(span, g) for g in gold_ranges.get(fault["path"], []))
