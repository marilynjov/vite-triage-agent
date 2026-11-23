#!/bin/sh
# Run the nightly retrieval eval from the repo root and keep a log. launchd calls
# this (scripts/com.vitetriage.nightly.plist); the exit code is eval.nightly's:
# 0 same or first, 1 a metric moved, 2 the run failed (Postgres or Ollama down).
cd "$(dirname "$0")/.." || exit 2
mkdir -p data/nightly
.venv/bin/python -m eval.nightly >> data/nightly/nightly.log 2>&1
