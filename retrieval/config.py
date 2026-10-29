"""Retrieval knobs. Every one of these is a variable you will measure."""
from __future__ import annotations

import os

from dotenv import load_dotenv

from mining import config as M

# Same precedence as mining/config.py: ENV_FILE, then .env.
load_dotenv(M.ROOT / os.getenv("ENV_FILE", ".env"))
load_dotenv(M.ROOT / ".env")

# Which repo this index belongs to. Taken from the same values the miner uses,
# so `ENV_FILE=.env.bowling` points mining AND retrieval at the same corpus
# without a second setting to forget.
CORPUS = f"{M.OWNER}/{M.NAME}"

# What the CLI and the API search with when not told otherwise: the best
# strategy measured on Vite val (recall@10 68.5% clean). The eval harness keeps
# its own explicit --strategy, so changing this never moves a recorded number.
DEFAULT_STRATEGY = os.getenv("STRATEGY", "fixed_v1_header")

# tree-sitter grammar for chunking v2. Empty disables the parser and falls back
# to v1+header, which works on any language.
TREE_SITTER_LANGUAGE = os.getenv("TREE_SITTER_LANGUAGE", "typescript")

EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://triage:triage@localhost:5433/triage"
)

# nomic-embed-text is trained with task prefixes. Measured on one spot check,
# adding them raised the similarity of both a relevant and an irrelevant chunk
# by the same amount — the margin did not move. Kept on because it is the
# documented usage, and left as a flag because "does it actually help recall"
# is answerable with the harness rather than by argument.
USE_TASK_PREFIX = os.getenv("USE_TASK_PREFIX", "1") == "1"
DOC_PREFIX = "search_document: " if USE_TASK_PREFIX else ""
QUERY_PREFIX = "search_query: " if USE_TASK_PREFIX else ""

# Chunking v1: fixed character windows, deliberately naive. The overlap exists
# so a construct straddling a boundary appears whole in at least one chunk —
# which is also the strongest argument for why v2 should split on syntax.
CHUNK_CHARS = int(os.getenv("CHUNK_CHARS", "1200"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))

EMBED_BATCH = int(os.getenv("EMBED_BATCH", "64"))

# A unit bigger than this gets sub-windowed. Some Vite functions are 400 lines;
# embedding one as a single chunk dilutes it to mush.
MAX_UNIT_CHARS = int(os.getenv("MAX_UNIT_CHARS", "2400"))
