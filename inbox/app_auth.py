"""Post as a GitHub App: a JWT signed with the app's key, exchanged for an installation token.

GitHub Apps authenticate in two steps. A JWT (RS256, at most 10 minutes long)
signed with the app's private key proves "I am app N"; it is then exchanged for
an installation token, valid for an hour and only for the repos the app is
installed on, which is what posts the comment (as `<app-name>[bot]`).

RS256 is RSASSA-PKCS1-v1_5 over SHA-256, which `openssl dgst -sha256 -sign`
produces directly, so this needs no JWT or crypto library.
"""
from __future__ import annotations

import base64
import json
import subprocess
import time
from pathlib import Path

import httpx

from .github import API, GitHubError

HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id: str, key_path: Path, *, now: int | None = None) -> str:
    key_path = Path(key_path)
    if not key_path.is_file():
        raise GitHubError(f"GITHUB_APP_KEY_PATH: no private key at {key_path}")
    now = int(time.time()) if now is None else now
    head = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"iat": now - 60, "exp": now + 540, "iss": str(app_id)}  # 60 s for clock drift
    body = _b64url(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{head}.{body}".encode()
    r = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key_path)],
                       input=signing_input, capture_output=True)
    if r.returncode != 0:
        raise GitHubError(f"could not sign with {key_path}: {r.stderr.decode().strip()}")
    return f"{head}.{body}.{_b64url(r.stdout)}"


def installation_token(owner: str, name: str, app_id: str, key_path: Path, *,
                       transport: httpx.BaseTransport | None = None,
                       now: int | None = None) -> str:
    jwt = app_jwt(app_id, key_path, now=now)
    with httpx.Client(base_url=API, headers={**HEADERS, "Authorization": f"Bearer {jwt}"},
                      timeout=30.0, transport=transport) as http:
        found = http.get(f"/repos/{owner}/{name}/installation")
        if found.status_code == 401:
            raise GitHubError(f"GitHub rejected the app's JWT (401): check that GITHUB_APP_ID "
                              f"({app_id}) matches the private key, and that the system clock is right.")
        if found.status_code == 404:
            raise GitHubError(f"GitHub App {app_id} is not installed on {owner}/{name} (404): "
                              "open the app's settings, Install App, and choose that repository.")
        _ok(found, f"finding app {app_id}'s installation on {owner}/{name}")
        made = http.post(f"/app/installations/{found.json()['id']}/access_tokens")
        _ok(made, f"getting an installation token for app {app_id}")
        return made.json()["token"]


def _ok(r: httpx.Response, doing: str) -> None:
    """Any other non-2xx becomes one readable line, never a traceback."""
    if r.is_success:
        return
    try:
        detail = r.json().get("message", "")
    except ValueError:
        detail = r.text[:200]
    raise GitHubError(f"GitHub refused {doing} ({r.status_code}): {detail}")
