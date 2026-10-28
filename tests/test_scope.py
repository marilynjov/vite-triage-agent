"""check_in_scope: the model may only name real, non-test source files at the pin."""
from __future__ import annotations

import pytest

from agent import tools
from api import schema as S


def _result(files: list[str]) -> S.GroundedTriageResult:
    return S.GroundedTriageResult(
        reasoning="r", issue_type="bug", severity="medium",
        suspected_components=[], reproduction_steps=[], confidence=0.5,
        suspected_files=files,
    )


def test_in_scope_source_file_passes():
    tools.check_in_scope(_result(["packages/vite/src/node/plugins/css.ts"]))


def test_test_file_is_rejected():  # exists at the pin, but a test file
    with pytest.raises(ValueError):
        tools.check_in_scope(_result(["packages/vite/src/node/__tests__/build.spec.ts"]))


def test_nonexistent_path_is_rejected():
    with pytest.raises(ValueError, match="nope.ts"):
        tools.check_in_scope(_result(["packages/vite/src/node/nope.ts"]))
