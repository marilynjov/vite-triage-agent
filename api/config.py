"""Settings for the API. Same pattern as mining/config.py — one place, .env-fed."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://triage:triage@localhost:5433/triage"
)

# Which provider answers. "ollama" runs on this machine and costs nothing;
# "anthropic" costs money and is where the gate numbers come from. Develop on
# the first, quote the second.
PROVIDER = os.getenv("PROVIDER", "ollama")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")

# Haiku by default because it is ~13x cheaper per triage than Opus. Switch to
# claude-opus-5 for gate runs and for Phase 07's quality-per-dollar comparison.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")

# Output ceiling. Hitting it truncates mid-thought and you pay for the call
# anyway, so keep it generous.
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16000"))

SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"


def active_model() -> str:
    """The model string to record in the calls table."""
    return OLLAMA_MODEL if PROVIDER == "ollama" else ANTHROPIC_MODEL
