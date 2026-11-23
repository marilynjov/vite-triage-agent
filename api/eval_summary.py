"""The eval dashboard's numbers, from files the evals already wrote. No model call.

Each section is built on its own and a failure empties only that section (and is
logged), so a half-written or missing file never makes the route 500. Arms are the
three on all 206 val rows (clean subset); staged outcomes come from the 40-row
staged run (clean); patch outcomes from the 06b patch file; the nightly history
from eval.nightly; the taxonomy from the hand labels. Only val files are read.
"""
from __future__ import annotations

import logging

from mining import config as M

from api.schema import ArmRow, EvalSummary, NightlyPoint, Share, TaxonomyCount
from eval import nightly, patch_eval, taxonomy
from eval.ablation import load_records
from eval.ablation_report import _use, arm_scores, cost_stats, outcome_table
from eval.patch_report import summarize

log = logging.getLogger(__name__)

ARMS_FILE = M.DATA / "runs" / "val-n206-r1.jsonl"
STAGED_FILE = M.DATA / "runs" / "val-n40-r3.jsonl"
PATCH_FILE = patch_eval.OUT
NIGHTLY_FILE = nightly.HISTORY
LABELS_FILE = taxonomy.LABELS
ARMS = ("retrieval", "grounded", "agent")
NIGHTLY_POINTS = 30


def _section(name, build, empty):
    try:
        return build()
    except Exception as exc:
        log.warning("eval summary: %s section failed: %s", name, exc)
        return empty


def _nightly() -> list[NightlyPoint]:
    return [NightlyPoint(at=r["at"], ok=r["ok"], strategy=r["strategy"], n=r["n"],
                         recall_at_10_clean=r["clean"].get("recall@10") if r["ok"] else None,
                         mrr_clean=r["clean"].get("mrr") if r["ok"] else None,
                         changed=r["changed"], error=r["error"])
            for r in nightly.load_history(NIGHTLY_FILE)[-NIGHTLY_POINTS:]]


def _arms() -> list[ArmRow]:
    records = load_records(ARMS_FILE)
    rows = []
    for a in ARMS:
        scores = arm_scores(records, a, "clean")
        if not scores:
            continue
        rows.append(ArmRow(arm=a, runs=len(_use(records, a, "clean")),
                           recall_at_1=scores["recall_at_1"][0], recall_at_5=scores["recall_at_5"][0],
                           mrr=scores["rr"][0], per_triage=cost_stats(records, a)["per_triage"]))
    return rows


def _staged() -> list[Share]:
    table = outcome_table(load_records(STAGED_FILE), subset="clean")
    return [Share(name=o, n=None, share=v[0]) for o, v in table.items()]


def _patch() -> tuple[list[Share], float | None]:
    records = patch_eval.load_patch_records(PATCH_FILE)
    if not records:
        return [], None
    s = summarize(records, {})
    outs = sorted(s["outcomes"].items(), key=lambda kv: (-kv[1]["n"], kv[0]))
    return [Share(name=o, n=v["n"], share=v["share"]) for o, v in outs], s["patchable_pass_rate"]


def _taxonomy() -> list[TaxonomyCount]:
    return [TaxonomyCount(category=c, n=n) for c, n in taxonomy.counts(taxonomy.load_labels(LABELS_FILE))]


def summary() -> EvalSummary:
    patch_outcomes, pass_rate = _section("patch", _patch, ([], None))
    return EvalSummary(
        nightly=_section("nightly", _nightly, []),
        arms_source=ARMS_FILE.name,
        arms=_section("arms", _arms, []),
        staged_outcomes=_section("staged", _staged, []),
        patch_outcomes=patch_outcomes,
        patch_pass_rate=pass_rate,
        taxonomy=_section("taxonomy", _taxonomy, []),
    )
