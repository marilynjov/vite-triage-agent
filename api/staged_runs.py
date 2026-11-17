"""Stored staged runs for the run browser: the 06a records joined to their 06b patch.

Reads the files the evals already wrote, on every request (about 1 MB in all), so
there is nothing to invalidate. Within a file the last record per (issue, repeat)
wins, the rule the evals use. Nothing here calls a model, and only the val split
is read.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, ValidationError

from api.schema import (
    HypothesisResult, LocateResult, PatchView, RealFix, StagedRunDetail, StagedRunSummary,
    StagedStages, StoredToolCall, UnderstandResult,
)
from eval import patch_eval
from eval.ablation import val_rows

log = logging.getLogger(__name__)

STAGED_FILES = patch_eval.STAGED_FILES
PATCH_FILE = patch_eval.OUT

Key = tuple[int, int]
_STAGES: tuple[tuple[str, type[BaseModel]], ...] = (
    ("locate", LocateResult), ("understand", UnderstandResult), ("hypothesis", HypothesisResult))
_VERIFY = ("passed", "status", "seconds", "tail", "timed_out")


def load_runs() -> dict[Key, dict]:
    staged = [r for p in STAGED_FILES for r in patch_eval._load_staged(p)]
    return patch_eval.latest_staged(staged)


def load_patches() -> dict[Key, dict]:
    return {(r["issue"], r["repeat"]): r for r in patch_eval.load_patch_records(PATCH_FILE)}


def _rows() -> dict[int, dict]:
    return {r["issue"]: r for r in val_rows("val", patch_eval.ALL)}


def real_fix_diff(row: dict) -> RealFix | None:
    return None


def _summary(rec: dict, patch: dict | None, row: dict | None) -> dict:
    return {
        "issue": rec["issue"], "repeat": rec["repeat"],
        "title": (row or {}).get("title", ""),
        "outcome": rec.get("outcome") or "unknown",
        "failed_stage": rec.get("failed_stage"),
        "cost_usd": float(rec.get("cost_usd") or 0.0) + float((patch or {}).get("cost_usd") or 0.0),
        "patch_outcome": (patch or {}).get("outcome"),
    }


def list_runs() -> list[StagedRunSummary]:
    runs, patches, rows = load_runs(), load_patches(), _rows()
    return [StagedRunSummary(**_summary(runs[k], patches.get(k), rows.get(k[0])))
            for k in sorted(runs)]


def _stages(key: Key, stored: dict | None) -> StagedStages:
    out = {}
    for name, model in _STAGES:
        raw = (stored or {}).get(name)
        try:
            out[name] = model.model_validate(raw) if raw is not None else None
        except ValidationError as exc:
            log.warning("staged run %s: stage %s does not validate: %s", key, name, exc)
            out[name] = None
    return StagedStages(**out)


def _patch_view(rec: dict) -> PatchView:
    body = rec.get("patch") or {}
    verify = rec.get("verify")
    return PatchView(
        reasoning=body.get("reasoning"), edits=body.get("edits") or [],
        diff=rec.get("diff"), outcome=rec.get("outcome"),
        cost_usd=float(rec.get("cost_usd") or 0.0), error=rec.get("error"),
        verify={k: verify[k] for k in _VERIFY} if verify else None,
    )


def get_run(issue: int, repeat: int) -> StagedRunDetail | None:
    key = (issue, repeat)
    rec = load_runs().get(key)
    if rec is None:
        return None
    patch = load_patches().get(key)
    row = _rows().get(issue)
    return StagedRunDetail(
        **_summary(rec, patch, row),
        body=(row or {}).get("body", ""),
        gold=list(rec.get("gold") or []),
        stages=_stages(key, rec.get("stages")),
        stage_costs=rec.get("stage_costs") or {},
        error=rec.get("error"),
        fault_hit=rec.get("fault_hit"),
        tool_calls=[StoredToolCall(**t) for t in rec.get("tool_calls") or []],
        patch=_patch_view(patch) if patch else None,
        real_fix=real_fix_diff(row) if row else None,
    )
