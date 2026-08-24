"""Proofs (a) and (b) — deliberately in ONE module.

(a) is the restraint proof: with a mock LinkedIn where EVERY documented write
    endpoint succeeds, propose_edit leaves zero non-GET requests in the log.
(b) is its positive control: apply_proposal, against the SAME fixture, DOES
    record exactly the documented write call.

They live together because (a) alone proves nothing. If (b) ever fails, the
fixture is not really reachable/writable and (a)'s "zero writes" result would be
an artefact of a broken instrument rather than evidence of restraint. Read the
two results as a pair.
"""

from __future__ import annotations

import pytest

from linkedin_mcp import api, proposals
from tests.fixtures.granted_write import PERSON_ID, SAMPLE_PROFILE, GrantedWriteLinkedIn

ALL_SECTION_CHANGES = [
    ("headline", {"text": "Proposed headline"}),
    ("summary", {"text": "Proposed summary"}),
    ("position", {"action": "create", "fields": {"title": "Engineer"}}),
    ("position", {"action": "update", "entity_id": "POS1", "fields": {"title": "Lead"}}),
    ("position", {"action": "delete", "entity_id": "POS1"}),
    ("skill", {"action": "create", "fields": {"name": "Project Management"}}),
    ("skill", {"action": "update", "entity_id": "SK1", "fields": {"name": "Robotics"}}),
    ("skill", {"action": "delete", "entity_id": "SK1"}),
    ("education", {"action": "create", "fields": {"schoolName": "UC Berkeley"}}),
    ("education", {"action": "update", "entity_id": "ED1", "fields": {"degreeName": "BS"}}),
    ("education", {"action": "delete", "entity_id": "ED1"}),
]


def _client(mock: GrantedWriteLinkedIn) -> api.LinkedInClient:
    return api.LinkedInClient(access_token="test-token", transport=mock.transport)


# ---- (a) RESTRAINT -------------------------------------------------------


def test_propose_edit_writes_nothing_even_though_every_write_would_succeed():
    mock = GrantedWriteLinkedIn()
    client = _client(mock)

    made = [
        proposals.propose_edit(
            section,
            changes,
            person_id=PERSON_ID,
            profile=SAMPLE_PROFILE,
            client=client,
        )
        for section, changes in ALL_SECTION_CHANGES
    ]

    assert len(made) == len(ALL_SECTION_CHANGES)
    assert all(item["proposal_id"] for item in made)
    assert mock.non_get_requests == [], (
        "propose_edit sent a non-GET request to a mock that would have granted it: "
        f"{mock.calls()}"
    )


def test_propose_edit_sends_no_request_at_all_when_a_profile_is_supplied():
    mock = GrantedWriteLinkedIn()
    for section, changes in ALL_SECTION_CHANGES:
        proposals.propose_edit(
            section,
            changes,
            person_id=PERSON_ID,
            profile=SAMPLE_PROFILE,
            client=_client(mock),
        )
    assert mock.requests == []


def test_listing_and_discarding_proposals_writes_nothing():
    mock = GrantedWriteLinkedIn()
    created = proposals.propose_edit(
        "headline", {"text": "x"}, person_id=PERSON_ID, profile=SAMPLE_PROFILE
    )
    proposals.list_proposals()
    proposals.discard_proposal(created["proposal_id"])
    assert mock.non_get_requests == []


# ---- (b) POSITIVE CONTROL ------------------------------------------------


@pytest.mark.parametrize("section,changes", ALL_SECTION_CHANGES)
def test_apply_proposal_does_record_the_documented_write(section, changes):
    mock = GrantedWriteLinkedIn()
    proposal = proposals.propose_edit(
        section, changes, person_id=PERSON_ID, profile=SAMPLE_PROFILE
    )
    outcome = proposals.apply_proposal(proposal["proposal_id"], client=_client(mock))

    assert outcome["status"] == proposals.STATUS_APPLIED
    assert len(mock.non_get_requests) == 1

    recorded = mock.non_get_requests[0]
    assert recorded["method"] == proposal["request"]["method"]
    assert recorded["url"] == proposal["request"]["url"]
    assert recorded["body"] == proposal["request"]["json_body"]
    assert recorded["headers"]["x-restli-protocol-version"] == "2.0.0"


def test_positive_control_hits_the_exact_documented_headline_endpoint():
    mock = GrantedWriteLinkedIn()
    proposal = proposals.propose_edit(
        "headline", {"text": "Ocean robotics engineer"}, person_id=PERSON_ID
    )
    proposals.apply_proposal(proposal["proposal_id"], client=_client(mock))

    writes = [(c, u) for (c, u) in mock.calls() if c != "GET"]
    assert writes == [("POST", "https://api.linkedin.com/v2/people/(id:ABC123)")]
    assert mock.non_get_requests[0]["body"] == {
        "patch": {
            "$set": {
                "headline": {
                    "localized": {"en_US": "Ocean robotics engineer"},
                    "preferredLocale": {"country": "US", "language": "en"},
                }
            }
        }
    }


def test_apply_sends_exactly_one_request():
    mock = GrantedWriteLinkedIn()
    proposal = proposals.propose_edit(
        "skill", {"action": "create", "fields": {"name": "Robotics"}}, person_id=PERSON_ID
    )
    proposals.apply_proposal(proposal["proposal_id"], client=_client(mock))
    assert len(mock.non_get_requests) == 1
