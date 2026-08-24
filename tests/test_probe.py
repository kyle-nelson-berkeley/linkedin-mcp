"""Proof (g): the live-probe discriminator and its env-flag refusal.

THE PROBE ITSELF IS NEVER EXECUTED HERE. Only the pure discriminator function
and the refusal path are exercised; the refusal path raises before any client is
constructed, so no socket is ever opened.
"""

from __future__ import annotations

import pytest

from linkedin_mcp import probe


def test_403_is_expected_pre_approval():
    result = probe.discriminate(403, {"message": "Not enough permissions"})
    assert result["outcome"] == probe.EXPECTED_PRE_APPROVAL
    assert result["is_failure"] is False
    assert "partner" in result["explanation"].lower()


def test_403_without_a_helpful_body_is_still_pre_approval():
    assert probe.discriminate(403, "")["outcome"] == probe.EXPECTED_PRE_APPROVAL


@pytest.mark.parametrize(
    "body",
    [
        {"message": "invalid scope"},
        {"serviceErrorCode": 100, "message": "ACCESS_DENIED"},
        "permission-denied for this member",
    ],
)
def test_scope_and_access_messages_are_expected_pre_approval(body):
    result = probe.discriminate(401, body)
    assert result["outcome"] == probe.EXPECTED_PRE_APPROVAL


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
