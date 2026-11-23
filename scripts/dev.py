"""Start the whole app with one command: Postgres, the API and the web dev server.

    .venv/bin/python scripts/dev.py

Checks what it needs first and prints the fix for each thing missing, then runs
`docker compose up -d --wait`, uvicorn (--reload, port 8000) and Vite (port 5173).
Ctrl-C stops both; if either exits on its own, the other is stopped too. PROVIDER
and the rest come from .env as before.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.config import OLLAMA_HOST  # noqa: E402

API_PORT, WEB_PORT = 8000, 5173
EMBED_MODEL = "nomic-embed-text"
GRACE = 10  # seconds a process gets after terminate() before kill()


def _http_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.read().decode()


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def preflight(*, root: Path = ROOT, which=shutil.which, run=subprocess.run,
              http_get=_http_get, port_free=_port_free) -> list[str]:
    """One line per problem, each with its fix; [] when everything is ready."""
    problems = []
    if not (root / ".venv" / "bin" / "python").exists():
        problems.append("no .venv: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt")
    if not (root / "web" / "node_modules").exists():
        problems.append("web dependencies missing: npm --prefix web ci")
    if which("docker") is None or run(["docker", "info"], capture_output=True).returncode != 0:
        problems.append("Docker is not running: open -a Docker")
    try:
        names = [m["name"] for m in json.loads(http_get(f"{OLLAMA_HOST}/api/tags"))["models"]]
        if not any(n.split(":")[0] == EMBED_MODEL for n in names):
            problems.append(f"Ollama lacks the embedding model: ollama pull {EMBED_MODEL}")
    except Exception:
        problems.append(f"Ollama is not reachable at {OLLAMA_HOST}: ollama serve")
    for port in (API_PORT, WEB_PORT):
        if not port_free(port):
            problems.append(f"port {port} is in use: lsof -i :{port}")
    return problems


def _stop(procs) -> None:
    for p in procs:
        if p.poll() is None:
            p.terminate()
    for p in procs:
        try:
            p.wait(timeout=GRACE)
        except subprocess.TimeoutExpired:
            p.kill()


def supervise(procs, poll: float = 0.5, sleep=time.sleep) -> int:
    """Wait until any process exits or Ctrl-C; stop the rest; return the first exit code."""
    try:
        while True:
            for p in procs:
                code = p.poll()
                if code is not None:
                    _stop([q for q in procs if q is not p])
                    return code
            sleep(poll)
    except KeyboardInterrupt:
        _stop(procs)
        return 130


def main() -> int:
    problems = preflight()
    if problems:
        print("Not ready:\n" + "\n".join(f"  - {p}" for p in problems))
        return 1
    up = subprocess.run(["docker", "compose", "up", "-d", "--wait"], cwd=ROOT)
    if up.returncode != 0:
        return up.returncode
    procs = [
        subprocess.Popen([str(ROOT / ".venv" / "bin" / "python"), "-m", "uvicorn", "api.main:app",
                          "--port", str(API_PORT), "--reload"], cwd=ROOT),
        subprocess.Popen(["npm", "--prefix", "web", "run", "dev"], cwd=ROOT),
    ]
    print(f"API http://localhost:{API_PORT} · web http://localhost:{WEB_PORT} · Ctrl-C stops both")
    return supervise(procs)


if __name__ == "__main__":
    sys.exit(main())
