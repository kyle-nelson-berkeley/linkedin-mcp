"""OAuth 2.0 3-legged flow (docs/api-notes.md, source 5).

Shape, verbatim from the notes:

* authorization: ``GET https://www.linkedin.com/oauth/v2/authorization`` with
  ``response_type=code``, ``client_id``, ``redirect_uri``, ``state``, ``scope``
* token exchange: ``POST https://www.linkedin.com/oauth/v2/accessToken`` as
  ``application/x-www-form-urlencoded`` with ``grant_type=authorization_code``,
  ``code``, ``client_id``, ``client_secret``, ``redirect_uri``
* a ``state`` mismatch is treated as CSRF: respond 401 and abort — no exchange.

The redirect catcher is a ONE-SHOT localhost HTTP listener: it serves the single
callback and shuts down. It never talks to LinkedIn.
"""

from __future__ import annotations

import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx

from . import config

AUTHORIZATION_URL = "https://www.linkedin.com/oauth/v2/authorization"
ACCESS_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"

# r_basicprofile appears in docs/api-notes.md's sample token response and is
# grantable today. w_compliance (the Profile Edit permission) is a PRIVATE
# permission granted to select developers — requesting it before LinkedIn
# assigns it makes the authorization call fail with 401 invalid scope, which
# would strand even the read-token flow. So the DEFAULT asks only for the
# read scope; after partner approval, set LINKEDIN_SCOPE
# ("r_basicprofile w_compliance") in ~/.config/linkedin-mcp/.env and re-run
# auth_start.
DEFAULT_SCOPE = "r_basicprofile"
SCOPE_ENV_KEY = "LINKEDIN_SCOPE"

STATE_BYTES = 16
DEFAULT_CALLBACK_TIMEOUT = 300.0
_POLL_INTERVAL = 0.25

_SUCCESS_PAGE = (
    "<html><body><h1>Authorization complete</h1>"
    "<p>You can close this tab and return to your terminal.</p></body></html>"
)
_FAILURE_PAGE = (
    "<html><body><h1>Authorization failed</h1>"
    "<p>Check the terminal for details.</p></body></html>"
)


class OAuthError(RuntimeError):
    """Raised for any OAuth failure. Messages never contain a code or token."""


def _scope() -> str:
    return config.get(SCOPE_ENV_KEY, DEFAULT_SCOPE) or DEFAULT_SCOPE


def build_authorization_url(
    *,
    client_id: str | None = None,
    redirect_uri: str | None = None,
    scope: str | None = None,
    state: str | None = None,
) -> tuple[str, str]:
    """Return ``(url, state)``. The caller must keep ``state`` for the CSRF check."""
    client_id = client_id or config.require(config.KEY_CLIENT_ID)
    redirect_uri = redirect_uri or config.redirect_uri()
    state = state or secrets.token_urlsafe(STATE_BYTES)
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": scope or _scope(),
        }
    )
    return f"{AUTHORIZATION_URL}?{query}", state


def start_authorization(
    *,
    client_id: str | None = None,
    redirect_uri: str | None = None,
    scope: str | None = None,
) -> tuple[str, str]:
    """Begin a sign-in: return ``(url, state)`` and REMEMBER the state.

    The state is persisted (0600, in the config dir) and reused by the next
    call while it is fresh, so the URL printed by ``auth_start(url_only=True)``
    and the URL the listener expects carry the SAME state. Without that, the
    callback from the already-open tab would be rejected as CSRF and the
    advertised two-step flow could never complete.
    """
    pending = config.load_pending_state()
    state = pending or secrets.token_urlsafe(STATE_BYTES)
    # Built first: a missing client id must fail before anything is persisted.
    url, _ = build_authorization_url(
        client_id=client_id, redirect_uri=redirect_uri, scope=scope, state=state
    )
    if not pending:
        config.save_pending_state(state)
    return url, state


def _listener_address(redirect_uri: str) -> tuple[str, int, str]:
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.scheme != "http":
        raise OAuthError(
            f"redirect_uri must be an http://localhost URL for the local catcher, "
            f"got scheme {parsed.scheme!r}"
        )
    host = parsed.hostname
    if host not in ("localhost", "127.0.0.1"):
        # The catcher is a plaintext local redirect target. Binding anywhere
        # beyond loopback would expose the OAuth callback (and the code it
        # carries) to the network. ::1 is rejected too: http.server's default
        # address family is IPv4, so accepting ::1 would accept a config that
        # always fails at bind time.
        raise OAuthError(
            "redirect_uri host must be IPv4 loopback (localhost or 127.0.0.1) "
            f"for the local catcher, got {host!r}"
        )
    port = parsed.port or 80
    return host, port, parsed.path or "/"


def wait_until_listening(host: str, port: int, timeout: float = 5.0) -> None:
    """Block until something accepts connections on host:port (test helper)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise OAuthError(f"listener did not come up on {host}:{port}")


def wait_for_callback(
    *,
    redirect_uri: str | None = None,
    expected_state: str,
    timeout: float = DEFAULT_CALLBACK_TIMEOUT,
) -> str:
    """Serve exactly one OAuth redirect and return the authorization code.

    Raises OAuthError on state mismatch (answering 401), on a LinkedIn-reported
    error, or on timeout. Never returns a code whose state did not match.
    """
    redirect_uri = redirect_uri or config.redirect_uri()
    host, port, expected_path = _listener_address(redirect_uri)
    outcome: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != expected_path:
                self._respond(404, "<html><body>not the callback path</body></html>")
                return
            params = dict(urllib.parse.parse_qsl(parsed.query))
            # State FIRST: a callback with a wrong/missing state is untrusted
            # whatever else it carries (api-notes.md: treat as CSRF, 401, abort).
            if params.get("state") != expected_state:
                outcome["error"] = "state mismatch on the OAuth callback (possible CSRF) — aborted before any token exchange"
                self._respond(401, _FAILURE_PAGE)
                return
            if params.get("error"):
                outcome["error"] = (
                    f"LinkedIn returned error={params['error']}: "
                    f"{params.get('error_description', 'no description')}"
                )
                self._respond(400, _FAILURE_PAGE)
                return
            code = params.get("code")
            if not code:
                outcome["error"] = "callback had no authorization code"
                self._respond(400, _FAILURE_PAGE)
                return
            outcome["code"] = code
            self._respond(200, _SUCCESS_PAGE)

        def _respond(self, status: int, body: str) -> None:
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: Any) -> None:
            """Silence stdout — this process speaks MCP on stdout."""

    try:
        server = HTTPServer((host, port), Handler)
    except OSError as exc:
        raise OAuthError(
            f"cannot listen on {host}:{port} for the OAuth redirect: {exc.strerror}"
        ) from exc

    server.timeout = _POLL_INTERVAL
    deadline = time.monotonic() + timeout
    try:
        while not outcome and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()

    if "code" in outcome:
        return str(outcome["code"])
    if "error" in outcome:
        raise OAuthError(str(outcome["error"]))
    raise OAuthError(
        f"timed out after {timeout:g}s waiting for the LinkedIn redirect to {redirect_uri}"
    )


def exchange_code(
    code: str,
    *,
    client_id: str | None = None,
    client_secret: str | None = None,
    redirect_uri: str | None = None,
    transport: httpx.BaseTransport | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Exchange an authorization code for a token and persist it (0600).

    Returns the NON-SECRET token status; the token itself is never returned.
    """
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id or config.require(config.KEY_CLIENT_ID),
        "client_secret": client_secret or config.require(config.KEY_CLIENT_SECRET),
        "redirect_uri": redirect_uri or config.redirect_uri(),
    }
    with httpx.Client(transport=transport, timeout=timeout) as client:
        response = client.post(
            ACCESS_TOKEN_URL,
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    try:
        payload = response.json()
    except ValueError:
        payload = {"error": "non-JSON response", "error_description": response.text[:200]}

    if not response.is_success:
        raise OAuthError(
            f"token exchange failed with HTTP {response.status_code}: "
            f"{payload.get('error', 'unknown')} — "
            f"{payload.get('error_description', 'no description')}"
        )

    access_token = payload.get("access_token")
    if not access_token:
        raise OAuthError("token exchange succeeded but the response had no access_token")

    config.save_token(
        access_token=access_token,
        expires_in=payload.get("expires_in"),
        refresh_token=payload.get("refresh_token"),
        scope=payload.get("scope"),
    )
    return config.token_status()


def run_authorization_flow(
    *,
    timeout: float = DEFAULT_CALLBACK_TIMEOUT,
    open_browser: bool = True,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    """Full flow: build URL, open it, catch one redirect, exchange, persist.

    Reuses the state from a preceding ``auth_start(url_only=True)`` preview so
    a tab the human already opened completes the flow, and clears it once the
    sign-in has succeeded (the state is single-use).
    """
    url, state = start_authorization()
    redirect_uri = config.redirect_uri()
    host, port, _path = _listener_address(redirect_uri)

    # The listener must be UP before the browser opens: a member with an
    # active session and prior grants is redirected immediately, and a
    # redirect that bounces off a closed port strands the flow until timeout.
    outcome: dict[str, Any] = {}

    def _listen() -> None:
        try:
            outcome["code"] = wait_for_callback(
                redirect_uri=redirect_uri, expected_state=state, timeout=timeout
            )
        except BaseException as exc:  # surfaced on the caller's thread below
            outcome["error"] = exc

    listener = threading.Thread(target=_listen, daemon=True)
    listener.start()
    try:
        wait_until_listening(host, port, timeout=5.0)
    except OAuthError:
        # Advisory only: a very fast callback can be served and the one-shot
        # server closed before this probe ever connects. The join below and
        # the outcome dict are the real authority on success or failure.
        pass

    if open_browser:
        _open_browser(url)
    listener.join(timeout=timeout + 10.0)
    if "error" in outcome:
        raise outcome["error"]
    if "code" not in outcome:
        raise OAuthError("the callback listener ended without a code or an error")
    code = outcome["code"]
    status = exchange_code(code, transport=transport)
    config.clear_pending_state()
    return {"authorization_url": url, "redirect_uri": redirect_uri, "token": status}


def _open_browser(url: str) -> None:
    """Best-effort browser launch in a thread; failure is not fatal."""
    import webbrowser

    def launch() -> None:
        try:
            webbrowser.open(url)
        except Exception:  # pragma: no cover - platform dependent
            pass

    threading.Thread(target=launch, daemon=True).start()
