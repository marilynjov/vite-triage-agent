"""inbox.app_auth: an RS256 JWT signed with openssl, exchanged for an installation token.
A throwaway key is generated per test; GitHub is a MockTransport."""
import base64
import json
import subprocess

import httpx
import pytest

from inbox import app_auth
from inbox.github import GitHubError

NOW = 1_800_000_000


@pytest.fixture
def key(tmp_path):
    k = tmp_path / "app.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(k), "2048"], check=True, capture_output=True)
    subprocess.run(["openssl", "rsa", "-in", str(k), "-pubout", "-out", str(tmp_path / "pub.pem")],
                   check=True, capture_output=True)
    return k


def _b64(part):
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def test_the_jwt_verifies_with_the_public_key_and_has_numeric_claims(key, tmp_path):
    token = app_auth.app_jwt("123", key, now=NOW)
    head, payload, sig = token.split(".")
    assert "=" not in token
    assert json.loads(_b64(head)) == {"alg": "RS256", "typ": "JWT"}
    assert json.loads(_b64(payload)) == {"iat": NOW - 60, "exp": NOW + 540, "iss": "123"}
    (tmp_path / "msg").write_bytes(f"{head}.{payload}".encode())
    (tmp_path / "sig").write_bytes(_b64(sig))
    v = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(tmp_path / "pub.pem"),
                        "-signature", str(tmp_path / "sig"), str(tmp_path / "msg")],
                       capture_output=True, text=True)
    assert v.returncode == 0 and "Verified OK" in v.stdout


def test_a_missing_key_is_a_clear_error(tmp_path):
    with pytest.raises(GitHubError, match="GITHUB_APP_KEY_PATH"):
        app_auth.app_jwt("123", tmp_path / "nope.pem", now=NOW)


def test_installation_token_finds_the_installation_then_exchanges_the_jwt(key):
    seen = []

    def handler(req):
        seen.append((req.method, req.url.path, req.headers["Authorization"]))
        if req.url.path == "/repos/acme/widgets/installation":
            return httpx.Response(200, json={"id": 42})
        assert req.url.path == "/app/installations/42/access_tokens"
        return httpx.Response(201, json={"token": "ghs_abc", "expires_at": "x"})

    tok = app_auth.installation_token("acme", "widgets", "123", key,
                                      transport=httpx.MockTransport(handler), now=NOW)
    assert tok == "ghs_abc"
    jwt = app_auth.app_jwt("123", key, now=NOW)
    assert seen == [("GET", "/repos/acme/widgets/installation", f"Bearer {jwt}"),
                    ("POST", "/app/installations/42/access_tokens", f"Bearer {jwt}")]


def test_an_app_not_installed_on_the_repo_says_so(key):
    t = httpx.MockTransport(lambda req: httpx.Response(404, json={"message": "Not Found"}))
    with pytest.raises(GitHubError, match=r"acme/widgets.*Install App"):
        app_auth.installation_token("acme", "widgets", "123", key, transport=t, now=NOW)


@pytest.mark.parametrize("step,status", [("lookup", 403), ("lookup", 502), ("token", 500),
                                         ("token", 422)])
def test_any_other_refusal_is_a_github_error_not_a_traceback(key, step, status):
    def handler(req):
        if req.url.path.endswith("/installation"):
            if step == "lookup":
                return httpx.Response(status, json={"message": "app suspended"})
            return httpx.Response(200, json={"id": 42})
        return httpx.Response(status, json={"message": "boom"})

    with pytest.raises(GitHubError, match=str(status)):
        app_auth.installation_token("acme", "widgets", "123", key,
                                    transport=httpx.MockTransport(handler), now=NOW)


def test_a_rejected_jwt_says_check_the_app_id_and_clock(key):
    t = httpx.MockTransport(lambda req: httpx.Response(401, json={"message": "bad jwt"}))
    with pytest.raises(GitHubError, match=r"rejected the app's JWT.*GITHUB_APP_ID.*clock"):
        app_auth.installation_token("acme", "widgets", "123", key, transport=t, now=NOW)
