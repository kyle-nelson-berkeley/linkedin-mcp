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

from linkedin_mcp import config, oauth


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


def test_token_exchange_rejects_response_without_access_token(isolated_config):
    config.write_values(
        {"LINKEDIN_CLIENT_ID": "cid-1", "LINKEDIN_CLIENT_SECRET": "sec-1"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"expires_in": 10})

    with pytest.raises(oauth.OAuthError):
        oauth.exchange_code("code", transport=httpx.MockTransport(handler))
    assert config.access_token() is None
