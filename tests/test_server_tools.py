"""Tool-level behaviour, including the restraint guarantee at the tool boundary.

The tools are called directly (FastMCP registers the plain functions), with the
LinkedInClient factory pointed at the granted-write mock so nothing leaves the
machine.
"""

from __future__ import annotations

import httpx
import pytest

from linkedin_mcp import api, config, proposals, server
from tests.fixtures.granted_write import PERSON_ID, GrantedWriteLinkedIn


@pytest.fixture
def mock_linkedin(monkeypatch):
    mock = GrantedWriteLinkedIn()
    monkeypatch.setattr(
        server, "_client", lambda: api.LinkedInClient("tok", transport=mock.transport)
    )
    config.write_values({config.KEY_ACCESS_TOKEN: "tok"})
    return mock


def test_auth_status_without_a_token(isolated_config):
    result = server.auth_status()
    assert result["ok"] is True
    assert result["has_token"] is False


def test_get_profile_without_a_token_reports_a_clean_error(isolated_config):
    result = server.get_profile()
    assert result["ok"] is False
    assert "auth_start" in result["message"]


def test_get_profile_reads_and_writes_nothing(mock_linkedin):
    result = server.get_profile()
    assert result["ok"] is True
    assert result["profile"]["id"] == PERSON_ID
    assert mock_linkedin.non_get_requests == []


def test_propose_edit_tool_returns_a_diff_and_writes_nothing(mock_linkedin):
    result = server.propose_edit("headline", {"text": "Ocean robotics engineer"})
    assert result["ok"] is True
    assert "Ocean robotics engineer" in result["diff"]
    assert result["request"]["url"] == "https://api.linkedin.com/v2/people/(id:ABC123)"
    assert mock_linkedin.non_get_requests == []


def test_propose_then_apply_at_the_tool_boundary(mock_linkedin):
    proposed = server.propose_edit("headline", {"text": "New"})
    applied = server.apply_proposal(proposed["proposal_id"])

    assert applied["ok"] is True
    assert applied["status"] == proposals.STATUS_APPLIED
    assert len(mock_linkedin.non_get_requests) == 1


def test_list_and_discard_tools(mock_linkedin):
    proposed = server.propose_edit("skill", {"action": "create", "fields": {"name": "x"}})
    listed = server.list_proposals()
    assert listed["count"] == 1
    assert listed["proposals"][0]["proposal_id"] == proposed["proposal_id"]

    discarded = server.discard_proposal(proposed["proposal_id"])
    assert discarded["ok"] is True
    assert server.list_proposals()["count"] == 0


def test_apply_unknown_proposal_returns_an_error_envelope(isolated_config):
    result = server.apply_proposal("deadbeef" * 4)
    assert result["ok"] is False
    assert result["error"] == "ProposalError"


def test_discard_unknown_proposal_returns_an_error_envelope(isolated_config):
    result = server.discard_proposal("deadbeef" * 4)
    assert result["ok"] is False


def test_propose_edit_bad_section_returns_an_error_envelope(isolated_config):
    result = server.propose_edit("interests", {"text": "x"}, person_id=PERSON_ID)
    assert result["ok"] is False
    assert "interests" in result["message"]


def test_auth_start_url_only_does_not_listen(isolated_config):
    config.write_values({config.KEY_CLIENT_ID: "cid"})
    result = server.auth_start(url_only=True)
    assert result["ok"] is True
    assert result["authorization_url"].startswith(
        "https://www.linkedin.com/oauth/v2/authorization?"
    )


def test_auth_start_without_client_id_returns_an_error_envelope(isolated_config):
    result = server.auth_start(url_only=True)
    assert result["ok"] is False
    assert result["error"] == "ConfigError"


def test_auth_start_flow_failure_is_reported_not_raised(isolated_config, monkeypatch):
    config.write_values({config.KEY_CLIENT_ID: "cid"})
    monkeypatch.setattr(
        server.oauth,
        "run_authorization_flow",
        lambda **kwargs: (_ for _ in ()).throw(server.oauth.OAuthError("timed out")),
    )
    result = server.auth_start(timeout_seconds=1)
    assert result["ok"] is False
    assert "timed out" in result["message"]


def test_apply_reports_expected_pre_approval_error(isolated_config, monkeypatch):
    config.write_values({config.KEY_ACCESS_TOKEN: "tok"})
    proposed = proposals.propose_edit("headline", {"text": "x"}, person_id=PERSON_ID)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Not enough permissions"})

    monkeypatch.setattr(
        server,
        "_client",
        lambda: api.LinkedInClient("tok", transport=httpx.MockTransport(handler)),
    )
    result = server.apply_proposal(proposed["proposal_id"])
    assert result["ok"] is False
    assert result["expected_pre_approval"] is True
    assert result["status"] == proposals.STATUS_PENDING


@pytest.mark.parametrize("status_code", [401, 500])
def test_get_profile_reports_a_failed_read_as_ok_false_with_the_status(
    isolated_config, monkeypatch, status_code
):
    config.write_values({config.KEY_ACCESS_TOKEN: "AQVsecret-value"})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"message": "Expired access token"})

    monkeypatch.setattr(
        server,
        "_client",
        lambda: api.LinkedInClient(
            "AQVsecret-value", transport=httpx.MockTransport(handler)
        ),
    )
    result = server.get_profile()

    assert result["ok"] is False
    assert result["status_code"] == status_code
    assert "profile" not in result
    assert str(status_code) in result["message"]
    assert "Expired access token" in result["message"]
    assert "AQVsecret-value" not in repr(result)


# --- round 5: explicit person_id must still diff against the current value ----


def test_propose_edit_with_explicit_person_id_diffs_against_current_value(mock_linkedin):
    """Supplying person_id (the normal follow-up to get_profile) must not
    degrade the approval diff to 'against empty' while a token is stored —
    the reviewer needs to see what the edit replaces."""
    result = server.propose_edit(
        "headline", {"text": "Ocean robotics engineer"}, person_id=PERSON_ID
    )
    assert result["ok"] is True
    assert "Current headline from the mock profile" in result["diff"]
    # and still zero writes on the wire
    assert mock_linkedin.non_get_requests == []
