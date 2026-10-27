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
# Context window for local calls. Ollama's default is small and it truncates
# an oversized prompt from the front without saying so. See ollama_provider.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))

# Haiku 5.5 by default: ~50x cheaper per triage than Opus 5.5 and 10x cheaper
# than Haiku 4.5. Switch to claude-opus-5-5 for gate runs and for Phase 07's
# quality-per-dollar comparison.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5")

# Output ceiling. Hitting it truncates mid-thought and you pay for the call
# anyway, so keep it generous.
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "16000"))

# How many retrieved files /triage/grounded shows the model. 10 matches the
# recall@10 that bounds what the model can find.
GROUNDED_K = int(os.getenv("GROUNDED_K", "10"))

SCHEMA_SQL = Path(__file__).resolve().parent / "schema.sql"


def active_model() -> str:
    """The model string to record in the calls table."""
    return OLLAMA_MODEL if PROVIDER == "ollama" else ANTHROPIC_MODEL
