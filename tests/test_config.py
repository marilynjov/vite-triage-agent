"""ENV_FILE must decide the corpus, whichever config module is imported first.

Configs load at import time, so each case runs in a fresh interpreter.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS_KEYS = ("REPO_OWNER", "REPO_NAME", "REPO_PATH", "DATA_DIR", "ENV_FILE")


def _resolve(tmp_path, first_import: str) -> list[str]:
    env_file = tmp_path / ".env.other"
    env_file.write_text("REPO_OWNER=acme\nREPO_NAME=widget\nREPO_PATH=/tmp/widget\nDATA_DIR=data/other\n")
    env = {k: v for k, v in os.environ.items() if k not in CORPUS_KEYS + ("PROVIDER",)}
    env["ENV_FILE"] = str(env_file)
    code = (f"import {first_import}\n"
            "from retrieval import config as R; from mining import config as M; from api import config as C\n"
            "print(R.CORPUS, M.REPO_PATH, M.DATA.name, C.PROVIDER, sep='|')")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, check=True).stdout
    return out.strip().split("|")


def _dotenv_provider() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("PROVIDER="):
            return line.split("=", 1)[1].strip()
    return "ollama"


def test_env_file_wins_when_the_api_config_loads_first(tmp_path):
    corpus, repo, data, provider = _resolve(tmp_path, "api.config")
    assert (corpus, repo, data) == ("acme/widget", "/tmp/widget", "other")
    # Keys the corpus file does not set still come from .env.
    assert provider == _dotenv_provider()


def test_env_file_wins_when_the_api_app_is_imported(tmp_path):
    corpus, repo, data, _ = _resolve(tmp_path, "api.main")
    assert (corpus, repo, data) == ("acme/widget", "/tmp/widget", "other")
