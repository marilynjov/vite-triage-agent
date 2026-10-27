"""The contract: what each result type's JSON Schema requires."""
from __future__ import annotations

from api import schema as S


def test_schema_for_grounded_result_requires_suspected_files():
    schema = S.json_schema(S.GroundedTriageResult)
    assert "suspected_files" in schema["properties"]
    assert "suspected_files" in schema["required"]
    assert schema["properties"]["suspected_files"]["maxItems"] == 5


def test_schema_defaults_to_the_phase_02_result():
    assert "suspected_files" not in S.json_schema()["properties"]
