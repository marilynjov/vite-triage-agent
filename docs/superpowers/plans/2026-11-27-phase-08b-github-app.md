# Phase 08b GitHub App Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The inbox posts as a GitHub App (`<app-name>[bot]`) instead of as Marilyn, using a short-lived installation token that reaches only the repos the app is installed on.

**Architecture:** This adds `inbox/app_auth.py`. It signs an RS256 JWT with the app's private key using the system `openssl` binary (no new Python dependency), finds the app's installation on the target repo, and exchanges the JWT for an installation token. `inbox.session.main` uses that token when `GITHUB_APP_ID` and `GITHUB_APP_KEY_PATH` are set, and falls back to `GITHUB_TOKEN` otherwise. Nothing else changes: the same `Repo` client and the same session questions.

**Tech Stack:** Python 3.12, httpx, `openssl` (LibreSSL on macOS, OpenSSL on Linux), pytest with `httpx.MockTransport`.

**Spec:** none. Agreed in conversation on 2026-11-27. The app (Issues: read and write, Metadata: read, webhooks off) is installed on the target repo only, and its key lives outside the repo.

### Decisions

| Decision | Choice |
|---|---|
| Signing | `openssl dgst -sha256 -sign <key>` over `base64url(header).base64url(payload)`, which is RSASSA-PKCS1-v1_5 with SHA-256, exactly RS256. No PyJWT or cryptography dependency. |
| JWT claims | `{"iat": now - 60, "exp": now + 540, "iss": "<app id>"}`. The 60 s backdate allows for clock drift; the total lifetime is under GitHub's 10-minute limit. |
| Installation | `GET /repos/{owner}/{repo}/installation` with the JWT gives `id`; `POST /app/installations/{id}/access_tokens` gives `token` (valid one hour). No installation ID to configure. |
| Config | `GITHUB_APP_ID` and `GITHUB_APP_KEY_PATH`, read with `os.getenv` after `mining.config` has loaded `ENV_FILE`. They go only in the corpus's gitignored env file. Both set means app; neither set means `GITHUB_TOKEN`; exactly one set is an error naming the missing one. |
| Errors | A missing key file, a failed openssl call, or a 401/404 from GitHub raise `GitHubError` with one line saying what to check: the key path, whether the app is installed on `owner/repo`, the App ID. `main` already prints `GitHubError` without a traceback. |
| Identity shown | `main` prints `posting as the GitHub App <id>` or `posting with GITHUB_TOKEN (as your account)` before the session starts. |

## Global Constraints

- No new dependencies. No network calls in tests; they generate a throwaway RSA key with `openssl genrsa` under `tmp_path`.
- The private key, its path, the App ID and anything from the private repo never enter the repo. `.env.example` gets the two variable names with placeholder values only.
- Commits carry no Co-Authored-By or other attribution lines.
- Use `.venv/bin/python` / `.venv/bin/pytest`.

## Review Focus

- A JWT that GitHub would reject: wrong base64url (padding left in), claims as strings instead of numbers, `exp` more than 10 minutes out. The test verifies the signature with `openssl dgst -verify` against the public key, and decodes the claims.
- The app not installed on the target repo (404 on `/repos/{o}/{r}/installation`): a message naming the repo and "Install App", not a traceback.
- Only one of the two variables set: an error, not a silent fall back to posting as Marilyn.

---

### Task 1: Sign the app JWT and get an installation token

**Files:**
- Create: `inbox/app_auth.py`, `tests/test_inbox_app_auth.py`

**Interfaces:**
- Produces:
  - `app_jwt(app_id: str, key_path: Path, *, now: int | None = None) -> str`
  - `installation_token(owner: str, name: str, app_id: str, key_path: Path, *, transport=None, now=None) -> str`
  - Both raise `inbox.github.GitHubError` on failure.

- [ ] **Step 1: Write the failing tests:**
  - `test_the_jwt_verifies_with_the_public_key_and_has_numeric_claims`: generate a key, sign, check the signature with `openssl dgst -sha256 -verify pub.pem -signature sig`, and decode the header and payload. Expect `{"alg": "RS256", "typ": "JWT"}`, `iat == now - 60`, `exp == now + 540`, `iss == "123"`, and no `=` padding in any part.
  - `test_a_missing_key_is_a_clear_error`
  - `test_installation_token_finds_the_installation_then_exchanges_the_jwt`: check the two requests' methods, paths and `Authorization: Bearer <jwt>`, and that the returned token is the one the mock gives.
  - `test_an_app_not_installed_on_the_repo_says_so`: a 404 gives a message containing `acme/widgets` and `Install App`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_inbox_app_auth.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "Inbox: GitHub App auth, a JWT signed with openssl exchanged for an installation token"`

### Task 2: Use the app's token in the session, and document it

**Files:**
- Modify: `inbox/session.py` (`main`), `README.md` ("Triage your own repo"), `.env.example`
- Test: `tests/test_inbox_session.py`

**Interfaces:**
- Produces: `session.github_token(*, getenv=os.getenv, exchange=installation_token) -> tuple[str, str]`, which returns `(token, "the GitHub App <id>" | "GITHUB_TOKEN (as your account)")`.

- [ ] **Step 1: Write the failing tests:**
  - `test_both_app_variables_use_the_app`
  - `test_neither_uses_github_token`
  - `test_only_one_app_variable_is_an_error_naming_the_other`
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement.** `main` calls `github_token()` inside its existing `GitHubError` handler and prints `posting as …` before `session(...)`. In the README, add the five-step app setup: create the app, set permissions, generate the key, Install App on only the repo, add the two variables to the corpus env file. Say that comments then appear as `<app-name>[bot]` and that the token lasts an hour. Add both variable names to `.env.example`, commented out.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: the whole suite passes.
- [ ] **Step 5: Commit.** `git commit -m "Inbox: post as the GitHub App when it is configured"`
