"""Stage contracts: typed results and boundary checks, against the real repo at the pin."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent import stages, tools
from api import schema as S

CSS = "packages/vite/src/node/plugins/css.ts"
OTHER = "packages/vite/src/node/config.ts"


def _loc(*paths: str) -> S.LocateResult:
    return S.LocateResult(reasoning="r", files=[
        S.LocatedFile(path=p, why="w", evidence="e") for p in paths])


def _region(path=CSS, start=1, end=10) -> S.Region:
    return S.Region(path=path, symbol=None, start_line=start, end_line=end,
                    what_it_does="d", relevance="r")


def _und(*regions: S.Region) -> S.UnderstandResult:
    return S.UnderstandResult(reasoning="r", regions=list(regions))


def _hyp(files=(CSS,), fault_path=CSS, start=1, end=5) -> S.HypothesisResult:
    return S.HypothesisResult(
        reasoning="r", issue_type="bug", severity="medium",
        suspected_components=[], reproduction_steps=[], confidence=0.5,
        suspected_files=list(files),
        fault=S.Fault(path=fault_path, start_line=start, end_line=end, mechanism="m"))


def test_models_validate_and_forbid_extra():
    assert _loc(CSS).files[0].path == CSS
    with pytest.raises(ValidationError):
        S.LocatedFile(path="a", why="w", evidence="e", extra=1)
    with pytest.raises(ValidationError):
        S.LocateResult(reasoning="r", files=[])
    with pytest.raises(ValidationError):
        _loc(*[f"f{i}" for i in range(6)])
    with pytest.raises(ValidationError):
        S.UnderstandResult(reasoning="r", regions=[])
    with pytest.raises(ValidationError):
        _und(*[_region() for _ in range(7)])
    with pytest.raises(ValidationError):
        S.Fault(path="a", start_line=1, end_line=2, mechanism="m", extra=1)
    assert issubclass(S.HypothesisResult, S.GroundedTriageResult)


def test_locate_accepts_in_scope_files_outside_candidates():
    stages.check_locate()(_loc(CSS, OTHER))


def test_locate_rejects_missing_or_out_of_scope_path():
    for bad in ("packages/vite/src/node/nope.ts",
                "packages/vite/src/node/__tests__/build.spec.ts"):
        with pytest.raises(ValueError, match="^locate: "):
            stages.check_locate()(_loc(CSS, bad))


def test_understand_rejects_region_outside_located_files():
    check = stages.check_understand(_loc(CSS))
    check(_und(_region()))
    with pytest.raises(ValueError, match="^understand: "):
        check(_und(_region(path=OTHER)))


def test_understand_rejects_out_of_bounds_lines():
    check = stages.check_understand(_loc(CSS))
    n = tools.file_length(CSS)
    check(_und(_region(start=1, end=n)))
    for s, e in ((1, n + 1), (0, 5), (10, 5)):
        with pytest.raises(ValueError, match="^understand: "):
            check(_und(_region(start=s, end=e)))


def test_hypothesis_rejects_fault_outside_understood_files():
    check = stages.check_hypothesis(_loc(CSS, OTHER), _und(_region()))
    check(_hyp())
    with pytest.raises(ValueError, match="^hypothesize: "):
        check(_hyp(fault_path=OTHER))
    with pytest.raises(ValueError, match="^hypothesize: "):
        check(_hyp(end=tools.file_length(CSS) + 1))
    with pytest.raises(ValueError, match="^hypothesize: "):
        check(_hyp(start=0))


def test_hypothesis_rejects_out_of_scope_suspected_file():
    check = stages.check_hypothesis(_loc(CSS), _und(_region()))
    with pytest.raises(ValueError, match="nope.ts"):
        check(_hyp(files=(CSS, "packages/vite/src/node/nope.ts")))


def test_file_length():
    assert tools.file_length(CSS) == 3685
    assert tools.file_length("packages/vite/src/node") is None
    assert tools.file_length("packages/vite/src/node/nope.ts") is None
    assert tools.file_length("../etc/passwd") is None
