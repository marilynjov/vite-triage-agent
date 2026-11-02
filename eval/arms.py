"""The ablation arms, each a (system prompt, tool list) pair for the one agent loop.

Adjacent arms differ in exactly one thing, so a score gap has one explanation:

  grounded        GROUNDED_SYSTEM, no tools     the one-call re-rank
  agent-no-tools  AGENT_SYSTEM, no tools        + the agent prompt
  agent           AGENT_SYSTEM, tools           + the tools
  agent-look      AGENT_SYSTEM + LOOK_FIRST,    + an instruction to use them
                  tools
  retrieval       no model                      the candidate order

All model arms go through agent.loop.run, so none differs by code path.
"""
from __future__ import annotations

from dataclasses import dataclass

from agent.loop import AGENT_SYSTEM
from agent.tools import TOOL_SPECS
from api.triage import GROUNDED_SYSTEM

LOOK_FIRST = (
    "If your conclusion depends on code or documents you cannot see in the "
    "excerpts, use a tool to check it before you conclude. Name a file outside "
    "the candidates only after a tool showed that it exists and fits the report."
)


@dataclass(frozen=True)
class Arm:
    name: str
    system: str | None  # None: no model call
    tool_specs: list[dict]
    kind: str = "loop"  # "pipeline": the staged pipeline, not the one agent loop


ARMS: dict[str, Arm] = {a.name: a for a in (
    Arm("grounded", GROUNDED_SYSTEM, []),
    Arm("agent-no-tools", AGENT_SYSTEM, []),
    Arm("agent", AGENT_SYSTEM, TOOL_SPECS),
    Arm("agent-look", AGENT_SYSTEM + "\n\n" + LOOK_FIRST, TOOL_SPECS),
    Arm("retrieval", None, []),
    Arm("staged", None, [], kind="pipeline"),
)}

MODEL_ARMS = ("grounded", "agent-no-tools", "agent", "agent-look")
PIPELINE_ARMS = ("staged",)
