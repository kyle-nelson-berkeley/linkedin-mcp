"""Proof (f): OAuth URL shape, one-shot listener, CSRF state check, token exchange.

The listener test drives the callback with a real HTTP GET to 127.0.0.1 — that
is the local redirect catcher, not LinkedIn. No request in this module leaves
the machine; the token exchange runs through httpx.MockTransport.
"""

from __future__ import annotations

import threading
import urllib.parse
import urllib.request

import httpx
import pytest

from linkedin_mcp import config, oauth, server


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}/callback"


def _hit(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


# ---- authorization URL ---------------------------------------------------


def test_authorization_url_has_documented_parameters(isolated_config):
    config.write_values({"LINKEDIN_CLIENT_ID": "cid-1"})
    url, state = oauth.build_authorization_url()

    parsed = urllib.parse.urlparse(url)
    query = dict(urllib.parse.parse_qsl(parsed.query))

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == oauth.AUTHORIZATION_URL
    assert query["response_type"] == "code"
    assert query["client_id"] == "cid-1"
    assert query["redirect_uri"] == config.DEFAULT_REDIRECT_URI
    assert query["state"] == state
    assert query["scope"] == oauth.DEFAULT_SCOPE
    assert "#" not in url


def test_authorization_state_is_random_per_call(isolated_config):
    config.write_values({"LINKEDIN_CLIENT_ID": "cid-1"})
    _, first = oauth.build_authorization_url()
    _, second = oauth.build_authorization_url()
    assert first != second
    assert len(first) >= 16


def test_authorization_url_requires_a_client_id(isolated_config):
    with pytest.raises(config.ConfigError):
        oauth.build_authorization_url()


# ---- pending state (url_only must not orphan its CSRF state) -------------


def test_start_authorization_persists_its_state_0600(isolated_config):
    config.write_values({"LINKEDIN_CLIENT_ID": "cid-1"})
    _, state = oauth.start_authorization()

    import os, stat

    path = config.pending_auth_path()
    assert path.exists()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert config.load_pending_state() == state


def test_start_authorization_reuses_the_pending_state(isolated_config):
    config.write_values({"LINKEDIN_CLIENT_ID": "cid-1"})
    first_url, first_state = oauth.start_authorization()
    second_url, second_state = oauth.start_authorization()

    assert second_state == first_state
    assert second_url == first_url


def test_a_stale_pending_state_is_not_reused(isolated_config):
    config.write_values({"LINKEDIN_CLIENT_ID": "cid-1"})
    _, first_state = oauth.start_authorization()

    import json
    import time

    path = config.pending_auth_path()
    path.write_text(
        json.dumps(
            {
                "state": first_state,
                "created_at": int(time.time()) - config.PENDING_STATE_TTL - 1,
            }
        )
    )
    assert config.load_pending_state() is None

    _, second_state = oauth.start_authorization()
    assert second_state != first_state


def test_clearing_the_pending_state_is_idempotent(isolated_config):
    config.clear_pending_state()
    assert config.load_pending_state() is None
    config.save_pending_state("st-1")
    config.clear_pending_state()
    assert config.load_pending_state() is None


def test_a_corrupt_pending_state_file_reads_as_absent(isolated_config):
    config.ensure_dir(config.config_dir())
    config.pending_auth_path().write_text("{not json")
    assert config.load_pending_state() is None


# ---- one-shot callback listener -----------------------------------------


def test_listener_returns_code_when_state_matches(isolated_config):
    port = _free_port()
    redirect_uri = _redirect_uri(port)
    captured: dict = {}

    def run():
        captured["result"] = oauth.wait_for_callback(
            redirect_uri=redirect_uri, expected_state="st-ok", timeout=10
        )

    thread = threading.Thread(target=run)
    thread.start()
    oauth.wait_until_listening("127.0.0.1", port, timeout=5)
    status, page = _hit(f"{redirect_uri}?code=the-code&state=st-ok")
    thread.join(timeout=10)

    assert status == 200
    assert "authorization complete" in page.lower()
    assert captured["result"] == "the-code"


def test_listener_rejects_state_mismatch_with_401_and_no_code(isolated_config):
    port = _free_port()
    redirect_uri = _redirect_uri(port)
    captured: dict = {}

    def run():
        try:
            oauth.wait_for_callback(
                redirect_uri=redirect_uri, expected_state="st-ok", timeout=10
            )
        except oauth.OAuthError as exc:
            captured["error"] = str(exc)

    thread = threading.Thread(target=run)
    thread.start()
    oauth.wait_until_listening("127.0.0.1", port, timeout=5)
    status, _ = _hit(f"{redirect_uri}?code=the-code&state=WRONG")
    thread.join(timeout=10)

    assert status == 401
    assert "state" in captured["error"].lower()
    assert "the-code" not in captured["error"]


def test_listener_surfaces_linkedin_error_parameters(isolated_config):
    port = _free_port()
    redirect_uri = _redirect_uri(port)
    captured: dict = {}

    def run():
        try:
            oauth.wait_for_callback(
                redirect_uri=redirect_uri, expected_state="st-ok", timeout=10
            )
        except oauth.OAuthError as exc:
            captured["error"] = str(exc)

    thread = threading.Thread(target=run)
    thread.start()
    oauth.wait_until_listening("127.0.0.1", port, timeout=5)
    _hit(f"{redirect_uri}?error=user_cancelled_authorize&error_description=nope&state=st-ok")
    thread.join(timeout=10)

    assert "user_cancelled_authorize" in captured["error"]


def test_listener_times_out_without_a_callback(isolated_config):
    port = _free_port()
    with pytest.raises(oauth.OAuthError) as excinfo:
        oauth.wait_for_callback(
            redirect_uri=_redirect_uri(port), expected_state="st", timeout=0.6
        )
    assert "timed out" in str(excinfo.value).lower()


# ---- token exchange ------------------------------------------------------


def test_token_exchange_posts_form_encoded_documented_fields(isolated_config):
    config.write_values(
        {"LINKEDIN_CLIENT_ID": "cid-1", "LINKEDIN_CLIENT_SECRET": "sec-1"}
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "AQVfake",
                "expires_in": 5184000,
                "refresh_token": "RFSHfake",
                "scope": "r_basicprofile",
            },
        )

    summary = oauth.exchange_code(
        "the-code", transport=httpx.MockTransport(handler)
    )

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == oauth.ACCESS_TOKEN_URL
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    form = dict(urllib.parse.parse_qsl(request.read().decode()))
    assert form == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "client_id": "cid-1",
        "client_secret": "sec-1",
        "redirect_uri": config.DEFAULT_REDIRECT_URI,
    }
    assert "AQVfake" not in repr(summary)
    assert summary["has_token"] is True


def test_token_exchange_persists_token_and_expiry(isolated_config):
    config.write_values(
        {"LINKEDIN_CLIENT_ID": "cid-1", "LINKEDIN_CLIENT_SECRET": "sec-1"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"access_token": "AQVfake", "expires_in": 5184000, "refresh_token": "R1"},
        )

    oauth.exchange_code("the-code", transport=httpx.MockTransport(handler))

    assert config.access_token() == "AQVfake"
    assert config.token_status()["expired"] is False
    assert config.get(config.KEY_REFRESH_TOKEN) == "R1"
    import os, stat

    assert stat.S_IMODE(os.stat(config.env_path()).st_mode) == 0o600


def test_token_exchange_failure_raises_without_persisting(isolated_config):
    config.write_values(
        {"LINKEDIN_CLIENT_ID": "cid-1", "LINKEDIN_CLIENT_SECRET": "sec-1"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_request"})

    with pytest.raises(oauth.OAuthError) as excinfo:
        oauth.exchange_code("bad", transport=httpx.MockTransport(handler))

    assert "invalid_request" in str(excinfo.value)
    assert config.access_token() is None


def test_url_only_preview_state_still_completes_the_flow(isolated_config):
    """The advertised two-step flow must actually finish.

    auth_start(url_only=True) hands the human a URL carrying a CSRF state. The
    later listener run must expect THAT state, otherwise the callback from the
    already-open tab is rejected as CSRF and the flow can never complete.
    """
    port = _free_port()
    redirect_uri = _redirect_uri(port)
    config.write_values(
        {
            "LINKEDIN_CLIENT_ID": "cid-1",
            "LINKEDIN_CLIENT_SECRET": "sec-1",
            config.KEY_REDIRECT_URI: redirect_uri,
        }
    )

    preview = server.auth_start(url_only=True)
    assert preview["ok"] is True
    preview_state = dict(
        urllib.parse.parse_qsl(urllib.parse.urlparse(preview["authorization_url"]).query)
    )["state"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"access_token": "AQVfake", "expires_in": 5184000}
        )

    captured: dict = {}

    def run():
        try:
            captured["result"] = oauth.run_authorization_flow(
                timeout=10, open_browser=False, transport=httpx.MockTransport(handler)
            )
        except oauth.OAuthError as exc:  # pragma: no cover - failure diagnostics
            captured["error"] = str(exc)

    thread = threading.Thread(target=run)
    thread.start()
    oauth.wait_until_listening("127.0.0.1", port, timeout=5)
    # The human clicks the tab opened from the PREVIEW url, so LinkedIn echoes
    # the preview's state back.
    status, _ = _hit(f"{redirect_uri}?code=the-code&state={preview_state}")
    thread.join(timeout=10)

    assert "error" not in captured, captured.get("error")
    assert status == 200
    assert captured["result"]["token"]["has_token"] is True
    assert config.access_token() == "AQVfake"
    # The one-shot state is consumed, so the next sign-in starts fresh.
    assert config.load_pending_state() is None


def test_a_stale_preview_state_is_still_rejected_as_csrf(isolated_config):
    """Reusing the pending state must not weaken the CSRF check."""
    port = _free_port()
    redirect_uri = _redirect_uri(port)
    config.write_values(
        {"LINKEDIN_CLIENT_ID": "cid-1", config.KEY_REDIRECT_URI: redirect_uri}
    )
    _, state = oauth.start_authorization()
    captured: dict = {}

    def run():
        try:
            oauth.wait_for_callback(
                redirect_uri=redirect_uri, expected_state=state, timeout=10
            )
        except oauth.OAuthError as exc:
            captured["error"] = str(exc)

    thread = threading.Thread(target=run)
    thread.start()
    oauth.wait_until_listening("127.0.0.1", port, timeout=5)
    status, _ = _hit(f"{redirect_uri}?code=the-code&state=SOMEONE-ELSES")
    thread.join(timeout=10)

    assert status == 401
    assert "state" in captured["error"].lower()


def test_token_exchange_rejects_response_without_access_token(isolated_config):
    config.write_values(
        {"LINKEDIN_CLIENT_ID": "cid-1", "LINKEDIN_CLIENT_SECRET": "sec-1"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expires_in": 10})

    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code("code", transport=httpx.MockTransport(handler))
    assert config.access_token() is None
