"""Proposal store behaviour + proof (d) for the proposal half of storage."""

from __future__ import annotations

import json
import os
import stat

import pytest

from linkedin_mcp import api, config, proposals
from tests.fixtures.granted_write import PERSON_ID, SAMPLE_PROFILE, GrantedWriteLinkedIn


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _headline_proposal(text: str = "New headline") -> dict:
    return proposals.propose_edit(
        "headline", {"text": text}, profile=SAMPLE_PROFILE
    )


def test_headline_proposal_records_documented_request(isolated_config):
    result = _headline_proposal()
    request = result["request"]
    assert request["method"] == "POST"
    assert request["url"] == "https://api.linkedin.com/v2/people/(id:ABC123)"
    assert request["json_body"]["patch"]["$set"]["headline"]["localized"] == {
        "en_US": "New headline"
    }
    assert "Authorization" not in request["headers"]
    assert result["status"] == proposals.STATUS_PENDING


def test_diff_shows_current_versus_proposed(isolated_config):
    result = _headline_proposal("Ocean robotics engineer")
    assert "Current headline from the mock profile" in result["diff"]
    assert "Ocean robotics engineer" in result["diff"]
    assert result["diff"].startswith("---")


def test_diff_against_empty_when_no_current_value_is_known(isolated_config):
    result = proposals.propose_edit(
        "skill",
        {"action": "create", "fields": {"name": "Project Management"}},
        person_id=PERSON_ID,
    )
    assert result["current_value"] == ""
    assert "Project Management" in result["diff"]


def test_summary_proposal_uses_richtext_shape(isolated_config):
    result = proposals.propose_edit(
        "summary", {"text": "Awesome summary of me."}, profile=SAMPLE_PROFILE
    )
    body = result["request"]["json_body"]["patch"]["$set"]["summary"]
    assert body["localized"] == {"en_US": {"rawText": "Awesome summary of me."}}
    assert "Current summary from the mock profile." in result["diff"]


def _localized_string(value: str, locale: str = "en_US") -> dict:
    language, country = locale.split("_")
    return {
        "localized": {locale: value},
        "preferredLocale": {"country": country, "language": language},
    }


@pytest.mark.parametrize(
    "section,sub,field",
    [
        ("position", "positions", "title"),
        ("skill", "skills", "name"),
        ("education", "educations", "schoolName"),
    ],
)
def test_sub_resource_create_update_delete_urls(isolated_config, section, sub, field):
    created = proposals.propose_edit(
        section, {"action": "create", "fields": {field: "x"}}, person_id=PERSON_ID
    )
    assert created["request"]["url"] == f"https://api.linkedin.com/v2/people/id=ABC123/{sub}"
    assert created["request"]["json_body"] == {field: _localized_string("x")}

    updated = proposals.propose_edit(
        section,
        {"action": "update", "entity_id": "E9", "fields": {field: "y"}},
        person_id=PERSON_ID,
    )
    assert updated["request"]["url"] == (
        f"https://api.linkedin.com/v2/people/id=ABC123/{sub}/E9"
    )
    assert updated["request"]["json_body"] == {
        "patch": {"$set": {field: _localized_string("y")}}
    }

    deleted = proposals.propose_edit(
        section, {"action": "delete", "entity_id": "E9"}, person_id=PERSON_ID
    )
    assert deleted["request"]["method"] == "DELETE"
    assert deleted["request"]["json_body"] is None


def test_skill_create_proposal_records_the_documented_multilocale_body(isolated_config):
    """api-notes.md, Skills CREATE minimal body — a plain string is normalized."""
    result = proposals.propose_edit(
        "skill",
        {"action": "create", "fields": {"name": "Project Management"}},
        person_id=PERSON_ID,
    )
    assert result["request"]["json_body"] == {
        "name": _localized_string("Project Management")
    }
    # The human-readable diff still shows the plain text, not the wire shape.
    assert "Project Management" in result["diff"]


def test_proposal_locale_flows_into_sub_resource_fields(isolated_config):
    result = proposals.propose_edit(
        "skill",
        {"action": "create", "fields": {"name": "Robótica"}},
        person_id=PERSON_ID,
        locale="es_ES",
    )
    assert result["request"]["json_body"] == {
        "name": _localized_string("Robótica", "es_ES")
    }


def test_invalid_locale_is_rejected_as_a_proposal_error(isolated_config):
    with pytest.raises(proposals.ProposalError):
        proposals.propose_edit(
            "skill",
            {"action": "create", "fields": {"name": "x"}},
            person_id=PERSON_ID,
            locale="english",
        )


def test_update_without_entity_id_is_rejected(isolated_config):
    with pytest.raises(proposals.ProposalError):
        proposals.propose_edit(
            "position", {"action": "update", "fields": {"title": "x"}}, person_id=PERSON_ID
        )


def test_unknown_section_is_rejected(isolated_config):
    with pytest.raises(proposals.ProposalError) as excinfo:
        proposals.propose_edit("interests", {"text": "x"}, person_id=PERSON_ID)
    assert "interests" in str(excinfo.value)


def test_unknown_action_is_rejected(isolated_config):
    with pytest.raises(proposals.ProposalError):
        proposals.propose_edit(
            "skill", {"action": "merge", "fields": {}}, person_id=PERSON_ID
        )


def test_headline_without_text_is_rejected(isolated_config):
    with pytest.raises(proposals.ProposalError):
        proposals.propose_edit("headline", {}, profile=SAMPLE_PROFILE)


def test_person_id_is_required(isolated_config):
    with pytest.raises(proposals.ProposalError):
        proposals.propose_edit("headline", {"text": "x"})


def test_proposal_files_are_0600_inside_a_0700_dir(isolated_config):
    result = _headline_proposal()
    path = config.proposals_dir() / f"{result['proposal_id']}.json"
    assert path.exists()
    assert _mode(path) == 0o600
    assert _mode(config.proposals_dir()) == 0o700
    assert _mode(config.config_dir()) == 0o700
    assert json.loads(path.read_text())["section"] == "headline"


def test_proposal_ids_are_unique(isolated_config):
    ids = {_headline_proposal(f"h{i}")["proposal_id"] for i in range(5)}
    assert len(ids) == 5


def test_list_and_load_and_discard(isolated_config):
    first = _headline_proposal("a")
    second = _headline_proposal("b")

    listed = proposals.list_proposals()
    assert {item["proposal_id"] for item in listed} == {
        first["proposal_id"],
        second["proposal_id"],
    }
    assert proposals.load_proposal(first["proposal_id"])["section"] == "headline"

    assert proposals.discard_proposal(first["proposal_id"])["discarded"] is True
    assert {item["proposal_id"] for item in proposals.list_proposals()} == {
        second["proposal_id"]
    }


def test_loading_an_unknown_proposal_raises(isolated_config):
    with pytest.raises(proposals.ProposalError):
        proposals.load_proposal("not-a-real-id")


def test_discarding_an_unknown_proposal_raises(isolated_config):
    with pytest.raises(proposals.ProposalError):
        proposals.discard_proposal("not-a-real-id")


def test_proposal_id_path_traversal_is_rejected(isolated_config):
    with pytest.raises(proposals.ProposalError):
        proposals.load_proposal("../../.env")


def test_applied_proposal_moves_to_applied_dir(isolated_config):
    mock = GrantedWriteLinkedIn()
    result = _headline_proposal()
    applied = proposals.apply_proposal(
        result["proposal_id"], client=_client(mock)
    )

    assert applied["status"] == proposals.STATUS_APPLIED
    assert not (config.proposals_dir() / f"{result['proposal_id']}.json").exists()
    stored = config.applied_dir() / f"{result['proposal_id']}.json"
    assert stored.exists()
    assert _mode(stored) == 0o600
    assert proposals.list_proposals() == []
    assert len(proposals.list_proposals(include_applied=True)) == 1


def test_applying_twice_is_refused(isolated_config):
    mock = GrantedWriteLinkedIn()
    result = _headline_proposal()
    proposals.apply_proposal(result["proposal_id"], client=_client(mock))
    with pytest.raises(proposals.ProposalError):
        proposals.apply_proposal(result["proposal_id"], client=_client(mock))


def test_failed_apply_keeps_the_proposal_pending_and_records_the_error(isolated_config):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Not enough permissions", "status": 403})

    client = api.LinkedInClient(
        access_token="tok", transport=httpx.MockTransport(handler)
    )
    result = _headline_proposal()
    outcome = proposals.apply_proposal(result["proposal_id"], client=client)

    assert outcome["status"] == proposals.STATUS_PENDING
    assert outcome["response"]["status_code"] == 403
    assert outcome["expected_pre_approval"] is True
    stored = proposals.load_proposal(result["proposal_id"])
    assert stored["last_response"]["status_code"] == 403


def test_a_bare_401_apply_failure_is_not_reported_as_expected_pre_approval(isolated_config):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    client = api.LinkedInClient(
        access_token="tok", transport=httpx.MockTransport(handler)
    )
    result = _headline_proposal()
    outcome = proposals.apply_proposal(result["proposal_id"], client=client)

    assert outcome["response"]["status_code"] == 401
    assert outcome["expected_pre_approval"] is False
    assert outcome["status"] == proposals.STATUS_PENDING


def test_looks_like_pre_approval_needs_a_marker_not_just_a_401_403():
    assert proposals.looks_like_pre_approval(403, "") is False
    assert proposals.looks_like_pre_approval(401, {"message": "Unauthorized"}) is False
    assert proposals.looks_like_pre_approval(403, {"message": "Not enough permissions"}) is True
    assert proposals.looks_like_pre_approval(401, {"message": "invalid scope"}) is True


@pytest.mark.parametrize("status_code", [400, 404, 405, 422, 500, 503])
def test_looks_like_pre_approval_needs_a_401_403_not_just_a_marker(status_code):
    assert proposals.looks_like_pre_approval(status_code, {"message": "invalid scope"}) is False
    assert proposals.looks_like_pre_approval(status_code, "permission denied") is False


@pytest.mark.parametrize("status_code", [400, 401, 403, 422, 500])
@pytest.mark.parametrize(
    "body", ["", {"message": "invalid scope"}, {"message": "Unauthorized"}, "permission"]
)
def test_looks_like_pre_approval_agrees_with_the_probe_discriminator(status_code, body):
    from linkedin_mcp import probe

    expected = probe.discriminate(status_code, body)["outcome"] == probe.EXPECTED_PRE_APPROVAL
    assert proposals.looks_like_pre_approval(status_code, body) is expected


def test_a_400_apply_failure_mentioning_permission_is_not_pre_approval(isolated_config):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "no permission for field 'patch'"})

    client = api.LinkedInClient(
        access_token="tok", transport=httpx.MockTransport(handler)
    )
    result = _headline_proposal()
    outcome = proposals.apply_proposal(result["proposal_id"], client=client)

    assert outcome["response"]["status_code"] == 400
    assert outcome["expected_pre_approval"] is False
    assert outcome["status"] == proposals.STATUS_PENDING


def _client(mock: GrantedWriteLinkedIn) -> api.LinkedInClient:
    return api.LinkedInClient(access_token="test-token", transport=mock.transport)
