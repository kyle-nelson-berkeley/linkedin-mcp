"""Proof (g): the live-probe discriminator and its env-flag refusal.

THE PROBE NEVER REACHES THE NETWORK HERE. The pure discriminator function and
the refusal path need no client at all (the refusal raises before one is built),
and the two preflight tests drive ``run_live_probe`` through an
``httpx.MockTransport`` whose handler fails loudly on any non-GET request.
"""

from __future__ import annotations

import httpx
import pytest

from linkedin_mcp import config, probe


def test_403_is_expected_pre_approval():
    result = probe.discriminate(403, {"message": "Not enough permissions"})
    assert result["outcome"] == probe.EXPECTED_PRE_APPROVAL
    assert result["is_failure"] is False
    assert "partner" in result["explanation"].lower()


@pytest.mark.parametrize("status_code", [401, 403])
@pytest.mark.parametrize("body", ["", {}, None, {"message": "Unauthorized"}])
def test_a_bare_401_403_without_a_marker_is_an_auth_error(status_code, body):
    result = probe.discriminate(status_code, body)
    assert result["outcome"] == probe.AUTH_ERROR
    assert result["is_failure"] is True
    explanation = result["explanation"].lower()
    assert "expired" in explanation
    assert "auth_start" in explanation


def test_auth_error_is_a_distinct_outcome_from_spec_error():
    assert probe.AUTH_ERROR != probe.SPEC_ERROR
    assert probe.discriminate(401, "")["outcome"] != probe.discriminate(404, "")["outcome"]


@pytest.mark.parametrize("status_code", [401, 403])
@pytest.mark.parametrize(
    "body",
    [
        {"message": "invalid scope"},
        {"serviceErrorCode": 100, "message": "ACCESS_DENIED"},
        "permission-denied for this member",
        {"message": "NOT_AUTHORIZED"},
        "this member is not permitted to perform that action",
    ],
)
def test_scope_and_access_messages_are_expected_pre_approval(body, status_code):
    result = probe.discriminate(status_code, body)
    assert result["outcome"] == probe.EXPECTED_PRE_APPROVAL
    assert result["is_failure"] is False


def test_a_marker_without_a_401_403_status_is_not_pre_approval():
    """A marker alone proves nothing: only LinkedIn's own auth statuses do.

    A 5xx that happens to contain the word "permission" is a server fault, not
    the partner gate, and must never be reported as a pass.
    """
    result = probe.discriminate(500, {"message": "invalid scope"})
    assert result["outcome"] != probe.EXPECTED_PRE_APPROVAL
    assert result["outcome"] == probe.UNKNOWN
    assert result["is_failure"] is True


@pytest.mark.parametrize("status_code", [400, 404, 405, 422])
def test_a_spec_error_status_stays_a_spec_error_whatever_the_body_says(status_code):
    result = probe.discriminate(
        status_code, {"message": "you do not have permission for field 'patch'"}
    )
    assert result["outcome"] == probe.SPEC_ERROR
    assert result["is_failure"] is True


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_a_5xx_with_a_marker_is_unknown(status_code):
    result = probe.discriminate(status_code, "ACCESS_DENIED by the edge proxy")
    assert result["outcome"] == probe.UNKNOWN
    assert result["is_failure"] is True


def test_404_is_a_spec_error():
    result = probe.discriminate(404, {"message": "Not Found"})
    assert result["outcome"] == probe.SPEC_ERROR
    assert result["is_failure"] is True
    assert "api-notes" in result["explanation"].lower()


def test_400_malformed_is_a_spec_error():
    result = probe.discriminate(400, {"message": "Unrecognized field \"patch\""})
    assert result["outcome"] == probe.SPEC_ERROR
    assert result["is_failure"] is True


def test_200_is_write_ok():
    result = probe.discriminate(200, {})
    assert result["outcome"] == probe.WRITE_OK
    assert result["is_failure"] is False


def test_unexpected_status_is_reported_as_unknown_not_silently_passed():
    result = probe.discriminate(503, {"message": "upstream unavailable"})
    assert result["outcome"] == probe.UNKNOWN
    assert result["is_failure"] is True


def test_probe_refuses_without_the_env_flag(isolated_config, monkeypatch):
    monkeypatch.delenv(probe.LIVE_PROBE_ENV, raising=False)
    with pytest.raises(probe.ProbeRefused) as excinfo:
        probe.run_live_probe()
    assert probe.LIVE_PROBE_ENV in str(excinfo.value)


def test_probe_refuses_when_the_env_flag_is_not_exactly_one(isolated_config, monkeypatch):
    monkeypatch.setenv(probe.LIVE_PROBE_ENV, "yes")
    with pytest.raises(probe.ProbeRefused):
        probe.run_live_probe()


def test_probe_with_flag_but_no_token_fails_before_any_network(isolated_config, monkeypatch):
    monkeypatch.setenv(probe.LIVE_PROBE_ENV, "1")

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the probe attempted to build a network client")

    monkeypatch.setattr(probe.httpx, "Client", explode)
    with pytest.raises(probe.ProbeError):
        probe.run_live_probe()


# --- preflight: a failed profile READ is an auth failure, not a missing id ----


def _probe_transport(status_code: int, body, seen: list):
    def handler(request):
        seen.append((request.method, str(request.url)))
        if request.method != "GET":
            raise AssertionError(
                "the probe sent a WRITE after the profile read failed: "
                f"{request.method} {request.url}"
            )
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "body", [{"message": "Expired access token"}, {"message": "Not enough permissions"}]
)
def test_preflight_401_is_an_auth_error_not_a_missing_id_error(
    isolated_config, monkeypatch, body
):
    monkeypatch.setenv(probe.LIVE_PROBE_ENV, "1")
    config.write_values({config.KEY_ACCESS_TOKEN: "tok"})
    seen: list = []

    result = probe.run_live_probe(transport=_probe_transport(401, body, seen))

    assert result["outcome"] == probe.AUTH_ERROR
    assert result["is_failure"] is True
    assert result["status_code"] == 401
    assert result["request"] == {"method": "GET", "url": "https://api.linkedin.com/v2/me"}
    assert seen == [("GET", "https://api.linkedin.com/v2/me")]


def test_preflight_404_on_the_profile_read_is_a_spec_error(isolated_config, monkeypatch):
    monkeypatch.setenv(probe.LIVE_PROBE_ENV, "1")
    config.write_values({config.KEY_ACCESS_TOKEN: "tok"})
    seen: list = []

    result = probe.run_live_probe(
        transport=_probe_transport(404, {"message": "Not Found"}, seen)
    )
    assert result["outcome"] == probe.SPEC_ERROR
    assert result["is_failure"] is True
    assert len(seen) == 1


# --- round 4: the probe must never invent a headline --------------------------


def _readonly_probe_transport(profile_body):
    """GET /v2/me returns the given profile; any write raises loudly."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v2/me":
            return httpx.Response(200, json=profile_body)
        raise AssertionError(
            f"probe sent a write it must not have: {request.method} {request.url}"
        )

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "profile_body",
    [
        {"id": "abc123"},                            # headline absent
        {"id": "abc123", "localizedHeadline": ""},   # empty
        {"id": "abc123", "localizedHeadline": {"localized": {}}},  # non-string
    ],
)
def test_probe_aborts_when_no_current_headline_is_readable(
    isolated_config, monkeypatch, profile_body
):
    """Without a real current headline the 'no-op' probe would MUTATE the
    profile (set it to a literal fallback string). It must abort instead."""
    monkeypatch.setenv(probe.LIVE_PROBE_ENV, "1")
    config.write_values({config.KEY_ACCESS_TOKEN: "tok"})
    with pytest.raises(probe.ProbeError, match="headline"):
        probe.run_live_probe(transport=_readonly_probe_transport(profile_body))


# --- round 9: opt-in must come from the PROCESS environment --------------------


def test_probe_opt_in_ignores_a_stale_value_in_the_env_file(isolated_config, monkeypatch):
    """A LINKEDIN_MCP_LIVE_PROBE=1 line persisted in ~/.config/linkedin-mcp/.env
    must NOT arm the probe: the opt-in is per-invocation and comes only from
    the process environment."""
    monkeypatch.delenv(probe.LIVE_PROBE_ENV, raising=False)
    config.write_values({probe.LIVE_PROBE_ENV: "1", config.KEY_ACCESS_TOKEN: "tok"})
    with pytest.raises(probe.ProbeRefused):
        probe.run_live_probe(transport=_readonly_probe_transport({"id": "x"}))
